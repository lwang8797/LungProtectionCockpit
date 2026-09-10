# -*- coding: utf-8 -*-
"""
api.py - M3+M4: FastAPI REST + WebSocket 实时服务

REST 端点:
  GET  /api/health            - 健康检查
  GET  /api/overview           - 总览仪表盘
  GET  /api/dp/trend           - ΔP 时间序列
  GET  /api/mp/trend           - MP 时间序列
  GET  /api/risk-map           - 二维风险图散点
  GET  /api/alerts             - 预警事件列表（含确认状态）
  POST /api/alerts/{id}/ack    - 确认单条预警（持久化）
  POST /api/alerts/ack-all     - 批量确认当前设备全部活动中预警
  GET  /api/metrics/1min       - 1分钟聚合数据

WebSocket:
  WS   /ws                     - 实时推送总览数据（每2秒，含批次到达即算的实时当前值）

静态:
  GET  /                       - 前端驾驶舱 HTML
  GET  /docs                    - Swagger API 文档
"""

import os
import math
import json
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, Query, HTTPException, WebSocket, WebSocketDisconnect, Body
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from bson import ObjectId
from bson.errors import InvalidId

from .config import (
    MONGO_URI, MONGO_DB, COLL_RAW, COLL_1MIN, COLL_ALERTS,
    DEVICE_ID, DP_THRESHOLD, MP_THRESHOLD, RISK_LABELS,
    DEFAULT_WINDOW_HOURS,
    COMPLIANCE_STRATUM_THRESHOLD, MAX_FORWARD_FILL_MIN, DCR_LOW_THRESHOLD,
    CUM_DP_OVER_HOURS_L3, CUM_DP_OVER_HOURS_L4,
    MP_HIGH_STRATUM_THRESHOLD, MP_LOW_STRATUM_THRESHOLD,
    CUM_MP_OVER_HOURS_L3_HIGH, CUM_MP_OVER_HOURS_L4_HIGH,
    CUM_MP_OVER_HOURS_L3_LOW, CUM_MP_OVER_HOURS_L4_LOW,
)
from .collector import get_db, get_time_range, collect_raw, get_current_work_mode, get_latest_raw_batch, to_float, PARAM_MAP
from .calculator import (
    enrich_rows, filter_ventilated, compute_exposure, compute_cumulative,
    build_points, weighted_mean_series,
    build_risk_map_points, classify_risk, classify_instant_risk,
)
from . import analyzer as ANZ

logger = logging.getLogger("lung_cockpit.api")

# ── 前端 HTML 路径 ──
_FRONTEND_HTML = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "cockpit_frontend.html",
)

app = FastAPI(
    title="肺保护驾驶舱 API",
    description="智能呼吸机 ΔP/MP 累积暴露监控服务 (REST + WebSocket)",
    version="0.3.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_db = None


def get_database():
    global _db
    if _db is None:
        _db = get_db()
    return _db


# ───────────────────────── 性能关键辅助 ─────────────────────────
# 趋势 / 风险图 / 总览的"当前值"一律优先读 metrics_1min（1 doc/min，已建索引），
# 避免对 raw measure_param（数百万文档）做全表扫描（原 6–9s → <1s）。
# 仅当 metrics_1min 尚无数据时回退到 collect_raw 实时计算。

DP_VALID_FLOOR = 3.0    # cmH2O，低于此视为待机/过渡脏值（如末分钟 DrivePress 跳变 2.0）
MP_VALID_FLOOR = 0.5    # J/min


def _clean_nan(obj):
    """递归把 float nan/±inf 变成 None，确保 JSON 严格合法。

    Starlette 的 JSONResponse 用 allow_nan=False 序列化，任何 NaN/Inf 都会 500。
    metrics_1min 中待机/缺参分钟会存 NaN（np.nan is not None 为 True，会漏过
    `is not None` 过滤），故在出口统一清洗。
    """
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, dict):
        return {k: _clean_nan(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean_nan(v) for v in obj]
    return obj


def _as_float(v) -> float:
    """MongoDB 里缺失/None 的数值 → NaN（积分时自动跳过）"""
    if v is None:
        return float("nan")
    try:
        return float(v)
    except (TypeError, ValueError):
        return float("nan")


def get_latest_minute_ts(db, device_id: str = DEVICE_ID) -> int:
    """取 metrics_1min 最新分钟（毫秒时间戳）；无聚合数据时返回 0。"""
    doc = db[COLL_1MIN].find_one({"deviceId": device_id}, sort=[("minute", -1)])
    return int(doc["minute"]) if doc else 0


def _latest_valid_ventilation(vent_docs: list) -> tuple:
    """从最近通气分钟向前找第一个「ΔP、MP 均有效」的分钟，返回 (dp_mean, mp_mean)。

    用于"当前值"口径：忽略 PEEP=OFF/待机、以及末分钟因参数跳变产生的脏值
    （如 ΔP=2.0 而真实通气约 9.0），取最近一次可信有效通气。
    """
    for d in reversed(vent_docs):
        dpv = d.get("dp_mean")
        mpv = d.get("mp_mean")
        if dpv is None or mpv is None:
            continue
        if isinstance(dpv, float) and math.isnan(dpv):
            continue
        if isinstance(mpv, float) and math.isnan(mpv):
            continue
        if dpv >= DP_VALID_FLOOR and mpv >= MP_VALID_FLOOR:
            return dpv, mpv
    return None, None


def _trend_from_1min(db, device_id, start_ts, latest_ts, field, threshold, points, dec):
    # 注意：返回窗口内「每一分钟」一个点（含待机分钟，value=None），
    # 以匹配前端按分钟对齐的时间轴（24h≈1440点）。待机分钟在前端按无效值处理。
    docs = list(db[COLL_1MIN].find(
        {"deviceId": device_id, "minute": {"$gte": start_ts, "$lte": latest_ts}},
        {"_id": 0, "minute": 1, field: 1},
    ).sort("minute", 1))
    if not docs:
        return None
    if len(docs) > points:
        step = len(docs) // points
        docs = docs[::step][:points]
    out = []
    for d in docs:
        v = d.get(field)
        out.append({
            "ts": d["minute"],
            "dt": datetime.fromtimestamp(d["minute"] / 1000, tz=timezone.utc).isoformat(),
            "value": round(v, dec) if v is not None else None,
            "threshold": threshold,
        })
    return out


def _trend_from_raw(db, device_id, start_ts, latest_ts, field, threshold, points, dec):
    rows = collect_raw(db, device_id, start_ts, latest_ts)
    enrich_rows(rows)
    if len(rows) > points:
        step = len(rows) // points
        rows = rows[::step][:points]
    return [{
        "ts": r["ts"],
        "dt": r["dt"].isoformat(),
        "value": round(r.get(field, float("nan")), dec) if not math.isnan(r.get(field, float("nan"))) else None,
        "threshold": threshold,
    } for r in rows]


def _riskmap_from_1min(db, device_id, start_ts, latest_ts, points):
    docs = list(db[COLL_1MIN].find(
        {"deviceId": device_id, "minute": {"$gte": start_ts, "$lte": latest_ts},
         "is_ventilating": True, "dp_mean": {"$ne": None}, "mp_mean": {"$ne": None}},
        {"_id": 0, "minute": 1, "dp_mean": 1, "mp_mean": 1, "risk_level": 1},
    ).sort("minute", 1))
    if not docs:
        return None
    if len(docs) > points:
        step = len(docs) // points
        docs = docs[::step][:points]
    return [{
        "dp": round(d["dp_mean"], 1),
        "mp": round(d["mp_mean"], 2),
        "minute": d["minute"],
        "risk_level": d.get("risk_level", 1),
    } for d in docs]


def _riskmap_from_raw(db, device_id, start_ts, latest_ts, points):
    rows = collect_raw(db, device_id, start_ts, latest_ts)
    enrich_rows(rows)
    vent_rows, _ = filter_ventilated(rows)
    use_rows = vent_rows if vent_rows else rows
    if len(use_rows) > points:
        step = len(use_rows) // points
        use_rows = use_rows[::step][:points]
    return build_risk_map_points(use_rows)


# ───────────────────────── 实时当前值（方案A） ─────────────────────────
# 设备上报稀疏（每数分钟一批），且 1 分钟聚合要等「下一分钟关门」才写；
# 为让仪表盘「设备一上报即秒级刷新」，这里直接从最新原始批次即时算 ΔP/MP/风险，
# 不依赖 metrics_1min。查询走 [deviceId,timeStamp] 索引，毫秒级、不扫全表。

def compute_live_current(db, device_id: str = DEVICE_ID) -> dict:
    """从最新原始批次即时计算当前 ΔP/MP/风险。

    返回 {valid, ts, dt, dp, mp, risk_level, risk_label, age_seconds}。
    valid=False 时（最新批次为待机/缺参）前端保留聚合值。
    """
    raw = get_latest_raw_batch(db, device_id)
    if not raw:
        return {"valid": False}
    row = {}
    for doc in raw:
        pid = doc.get("paramId")
        pname = PARAM_MAP.get(pid)
        if not pname:
            continue
        row[pname] = to_float(doc.get("value"))
    if not row:
        return {"valid": False}
    row["ts"] = int(raw[0]["timeStamp"])
    enrich_rows([row])
    dp = row.get("dP", float("nan"))
    mp = row.get("MP", float("nan"))
    if (math.isnan(dp) or dp <= 0) or math.isnan(mp):
        return {
            "valid": False,
            "ts": row["ts"],
            "dt": datetime.fromtimestamp(row["ts"] / 1000, tz=timezone.utc).isoformat(),
        }
    dp_over = dp > DP_THRESHOLD
    mp_over = mp > MP_THRESHOLD
    risk = classify_instant_risk(dp, mp)
    age = int(datetime.now(timezone.utc).timestamp() - row["ts"] / 1000.0)
    return {
        "valid": True,
        "ts": row["ts"],
        "dt": datetime.fromtimestamp(row["ts"] / 1000, tz=timezone.utc).isoformat(),
        "dp": round(dp, 1),
        "mp": round(mp, 2),
        # 计算模式溯源：dynamic 表示平台压不可信、已降级为 Ppeak−PEEP，界面打 *Dyn*
        "dp_source": row.get("dp_source", "none"),
        "mp_source": row.get("mp_source", "none"),
        "risk_level": risk,
        "risk_label": RISK_LABELS.get(risk, "L1 正常"),
        "age_seconds": age,
    }


def compute_snapshot(db, device_id: str = DEVICE_ID) -> dict:
    """通气参数快照：取最新原始批次的关键通气参数（VT/RR/PEEP/PIP/Pplat/FiO₂/I:E/RSBI）。

    与 compute_live_current 共享最新批次，但即使 ΔP/MP 无效（如待机）也返回该批次
    能解析到的参数，供前端「通气参数快照」实时展示，避免显示写死的占位值。
    直接按 paramId 映射（PARAM_MAP 命名不统一且部分参数未纳入，故此处显式绑定）。
    """
    # paramId -> (输出字段名, 是否为字符串)
    SNAP_FIELDS = {
        106: ("vt_ml", False),       # Vte 呼出潮气量 (mL)
        110: ("vt_insp_ml", False),  # Vti 吸入潮气量 (mL)
        113: ("rr", False),          # fTotal 总呼吸频率
        104: ("peep", False),        # PEEP
        101: ("pip", False),         # Ppeak 峰压
        102: ("pplat", False),       # Pplat 平台压
        107: ("fio2", False),        # FiO2
        117: ("rsbi", False),        # RSBI
        119: ("ie", True),           # I:E 比例（字符串，如 "2:1"）
    }
    raw = get_latest_raw_batch(db, device_id)
    if not raw:
        return {"valid": False}
    snap = {
        "valid": True,
        "ts": int(raw[0]["timeStamp"]),
        "dt": datetime.fromtimestamp(int(raw[0]["timeStamp"]) / 1000, tz=timezone.utc).isoformat(),
        "vt_ml": None, "vt_insp_ml": None, "rr": None, "peep": None,
        "pip": None, "pplat": None, "fio2": None, "rsbi": None, "ie": None,
    }
    for doc in raw:
        pid = doc.get("paramId")
        spec = SNAP_FIELDS.get(pid)
        if not spec:
            continue
        key, is_str = spec
        if is_str:
            snap[key] = doc.get("value")
        else:
            v = to_float(doc.get("value"))
            snap[key] = None if (v is None or (isinstance(v, float) and math.isnan(v))) else round(v, 1)
    if all(snap[k] is None for k in ("vt_ml", "rr", "peep", "pip", "pplat", "fio2")):
        snap["valid"] = False
    return snap


# ════════════════════ WebSocket 连接管理器 ════════════════════

class ConnectionManager:
    """管理 WebSocket 活跃连接，支持广播"""

    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)
        logger.info(f"WebSocket 连接, 当前 {len(self.active)} 个客户端")

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)
        logger.info(f"WebSocket 断开, 当前 {len(self.active)} 个客户端")

    async def broadcast(self, message: dict):
        """向所有活跃客户端推送消息"""
        text = json.dumps(message, ensure_ascii=False, default=str)
        dead = []
        for ws in self.active:
            try:
                await ws.send_text(text)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


manager = ConnectionManager()


# ── 累积暴露阈值（随响应下发，前端据此在「设置」中调整；待临床确认）──
CUM_THRESHOLDS = {
    "dp_over_hours_l3": CUM_DP_OVER_HOURS_L3,
    "dp_over_hours_l4": CUM_DP_OVER_HOURS_L4,
    "mp_high_over_hours_l3": CUM_MP_OVER_HOURS_L3_HIGH,
    "mp_high_over_hours_l4": CUM_MP_OVER_HOURS_L4_HIGH,
    "mp_low_over_hours_l3": CUM_MP_OVER_HOURS_L3_LOW,
    "mp_low_over_hours_l4": CUM_MP_OVER_HOURS_L4_LOW,
    "mp_high_threshold": MP_HIGH_STRATUM_THRESHOLD,
    "mp_low_threshold": MP_LOW_STRATUM_THRESHOLD,
    "compliance_stratum": COMPLIANCE_STRATUM_THRESHOLD,
}


def _cumulative_block(s: dict) -> dict:
    """构造总览 cumulative 块：高暴露小时数 + 顺应性分层 + 累积维度风险 + 默认阈值。

    入参 s:
      cum_over_minutes: {"dp","mp17","mp18","mp20"}  (高暴露分钟数)
      compliance_mean, energy_j, cum_auc_above: {"dp","mp17","mp18","mp20"}
      vent_min, cum_risk
      window（可选）: 由 _window_cumulative 产出的 24h 滚动口径
    """
    dp_over_min = s["cum_over_minutes"]["dp"] or 0.0
    mp18 = s["cum_over_minutes"]["mp18"] or 0.0
    mp20 = s["cum_over_minutes"]["mp20"] or 0.0
    comp = s.get("compliance_mean") or 0.0
    stratum = "high" if comp > COMPLIANCE_STRATUM_THRESHOLD else "low"

    dp_over_hours = dp_over_min / 60.0
    mp_over_hours_high = mp18 / 60.0
    mp_over_hours_low = mp20 / 60.0
    mp_over_hours = mp_over_hours_high if stratum == "high" else mp_over_hours_low

    l3 = CUM_MP_OVER_HOURS_L3_HIGH if stratum == "high" else CUM_MP_OVER_HOURS_L3_LOW
    l4 = CUM_MP_OVER_HOURS_L4_HIGH if stratum == "high" else CUM_MP_OVER_HOURS_L4_LOW
    mp_thr = MP_HIGH_STRATUM_THRESHOLD if stratum == "high" else MP_LOW_STRATUM_THRESHOLD

    vent_min = s.get("vent_min") or 0.0
    block = {
        "dp_over_hours": round(dp_over_hours, 2),
        "mp_over_hours_high": round(mp_over_hours_high, 2),
        "mp_over_hours_low": round(mp_over_hours_low, 2),
        "mp_over_hours": round(mp_over_hours, 2),
        "compliance_stratum": stratum,
        "compliance_mean": round(comp, 1) if s.get("compliance_mean") else None,
        "energy_kj": round((s.get("energy_j") or 0) / 1000.0, 2),
        # AUC 统一换算为「值单位 × 小时」（内部按分钟存储）
        "dp_auc_above": round((s["cum_auc_above"]["dp"] or 0) / 60.0, 2),
        "mp_auc_above_17": round((s["cum_auc_above"]["mp17"] or 0) / 60.0, 2),
        "mp_auc_above_18": round((s["cum_auc_above"]["mp18"] or 0) / 60.0, 2),
        "mp_auc_above_20": round((s["cum_auc_above"]["mp20"] or 0) / 60.0, 2),
        # 有效通气时长 / 断流 / PTA（URS FR-04）
        "vent_duration_min": round(vent_min, 1),
        "vent_hours": round(vent_min / 60.0, 2),
        "gap_minutes": round(s.get("gap_min") or 0.0, 1),
        "n_gaps": s.get("n_gaps", 0),
        "dp_pta": round((dp_over_hours / (vent_min / 60.0) * 100.0)
                        if vent_min > 0 else 0.0, 1),
        "mp_pta": round((((s["cum_over_minutes"]["mp17"] or 0.0) / 60.0)
                         / (vent_min / 60.0) * 100.0) if vent_min > 0 else 0.0, 1),
        # 计算模式溯源：dynamic 时界面需打 *Dyn*
        "dp_source": s.get("dp_source", "none"),
        "mp_source": s.get("mp_source", "none"),
        "risk_level": s.get("cum_risk", 1),
        "risk_label": RISK_LABELS.get(s.get("cum_risk", 1), "L1 正常"),
        "thresholds": CUM_THRESHOLDS,
        # 默认阈值下的越限标志（前端会用设置中的 CUM 重新判定）
        "alarms": {
            "dp_over": dp_over_hours >= CUM_DP_OVER_HOURS_L3,
            "mp_over": mp_over_hours >= l3,
        },
        "_mp_threshold_used": mp_thr,
        "_mp_l3_used": l3,
        "_mp_l4_used": l4,
    }

    # 24h 滚动窗口口径（URS G0~G3 判据全部基于 "x h/24h"）
    w = s.get("window")
    if w:
        block["window"] = w
    return block


def _window_cumulative(db, device_id: str, start_ts: int, latest_ts: int,
                       window_hours: float) -> dict:
    """按 URS 口径计算「滚动窗口」内的暴露剂量（默认 24 h）。

    与「通气全程累计」不同：这里只读窗口内的 metrics_1min 分钟文档，
    按真实时间积分（≤4h 前向填充 / >4h 断流），得到 TAT、AUC、PTA、DCR。
    风险矩阵 G0~G3 的判据（如 TAT < 5 h/24h）应使用这里的数值。
    """
    docs = list(db[COLL_1MIN].find(
        {"deviceId": device_id, "minute": {"$gte": start_ts, "$lte": latest_ts},
         "dp_mean": {"$ne": None}, "mp_mean": {"$ne": None}},
        {"_id": 0, "minute": 1, "dp_mean": 1, "mp_mean": 1, "vt_mean": 1},
    ).sort("minute", 1))

    if not docs:
        return None

    rows = []
    for d in docs:
        dp, mp, vt = _as_float(d.get("dp_mean")), _as_float(d.get("mp_mean")), \
            _as_float(d.get("vt_mean"))
        rows.append({
            "ts": d["minute"], "dP": dp, "MP": mp,
            "CRS": (vt / dp) if (not math.isnan(vt) and not math.isnan(dp) and dp > 0)
                   else float("nan"),
        })

    cum = compute_cumulative(rows, MAX_FORWARD_FILL_MIN)

    # 顺应性：按有效通气时长加权（与聚合层的全程累计同一口径 weighted_mean_series）
    crs_pts = build_points(rows, "CRS")
    comp = weighted_mean_series(crs_pts, MAX_FORWARD_FILL_MIN)
    stratum = "high" if (not math.isnan(comp)
                         and comp > COMPLIANCE_STRATUM_THRESHOLD) else "low"

    # 窗口 DCR：分母用窗口自然时长（URS FR-01 的 24h 口径）
    win_min = window_hours * 60.0
    dcr_window = (cum["vent_minutes"] / win_min * 100.0) if win_min > 0 else 0.0

    return {
        "hours": window_hours,
        "vent_hours": round(cum["vent_hours"], 2),
        "gap_minutes": round(cum["gap_minutes"], 1),
        "n_gaps": cum["n_gaps"],
        "dp_tat_hours": round(cum["dp_tat_hours"], 2),
        "mp_tat_hours": {k: round(v, 2) for k, v in cum["mp_tat_hours"].items()},
        "dp_auc_h": round(cum["dp_auc_h"], 2),
        "mp_auc_h": {k: round(v, 2) for k, v in cum["mp_auc_h"].items()},
        "dp_pta": round(cum["dp_pta"], 1),
        "mp_pta": round(cum["mp_pta"], 1),
        "energy_kj": round(cum["energy_j"] / 1000.0, 2),
        "compliance_mean": round(comp, 1) if not math.isnan(comp) else None,
        "compliance_stratum": stratum,
        "dcr": round(dcr_window, 1),
        # 原始上报密度：有数据的分钟数 / 窗口自然分钟数（不含 4h 前向填充）
        "dcr_raw": round(len(rows) / win_min * 100.0, 1) if win_min > 0 else 0.0,
        "dcr_low": dcr_window < DCR_LOW_THRESHOLD,
        "dcr_threshold": DCR_LOW_THRESHOLD,
        "points": len(rows),
    }


def _minute_docs_to_series(docs: list) -> list:
    """把 metrics_1min 文档转成 analyzer 用的连续分钟序列（只保留有 dp/mp 的分钟）。"""
    rows = []
    for d in docs:
        dp = _as_float(d.get("dp_mean"))
        mp = _as_float(d.get("mp_mean"))
        if math.isnan(dp) or math.isnan(mp):
            continue
        vt = _as_float(d.get("vt_mean"))
        crs = (vt / dp) if (not math.isnan(vt) and dp > 0) else float("nan")
        rows.append({"ts": d["minute"], "dp": dp, "mp": mp, "crs": crs})
    rows.sort(key=lambda r: r["ts"])
    return rows


def _compute_analysis(device_id: str = DEVICE_ID, hours: float = DEFAULT_WINDOW_HOURS,
                      series: Optional[list] = None) -> dict:
    """运行 URS FR-05（斜率/CUSUM）+ FR-06（G0-G3 防抖）分析。

    若给定 series（analyzer 行：ts/dp/mp/crs），直接用它（测试注入用）；
    否则从 metrics_1min 读窗口数据构造。
    """
    db = get_database()
    latest_ts = get_latest_minute_ts(db, device_id)
    if series is None:
        if latest_ts == 0:
            _, latest_ts = get_time_range(db, device_id)
        if latest_ts == 0:
            return {"error": "no_data", "device": device_id}
        start_ts = latest_ts - int(hours * 3600 * 1000)
        docs = list(db[COLL_1MIN].find(
            {"deviceId": device_id, "minute": {"$gte": start_ts, "$lte": latest_ts}},
            {"_id": 0, "minute": 1, "dp_mean": 1, "mp_mean": 1, "vt_mean": 1,
             "is_ventilating": 1},
        ).sort("minute", 1))
        series = _minute_docs_to_series(docs)

    if not series:
        return {"error": "no_data", "device": device_id, "hours": hours}

    # ── 24h TAT（用于 G0-G3 判据）——用与 _window_cumulative 相同口径 ──
    # 直接把 series 还原成 compute_cumulative 输入，取 dp_tat_hours / mp_tat_hours
    cum_rows = [{"ts": r["ts"], "dP": r["dp"], "MP": r["mp"],
                 "CRS": r.get("crs", float("nan"))} for r in series]
    cum = compute_cumulative(cum_rows, MAX_FORWARD_FILL_MIN)
    dp_tat_24h = cum["dp_tat_hours"]
    mp_tat_24h = cum["mp_tat_hours"].get("mp17", 0.0)

    # 顺应性（按有效通气时长加权，与 overview 一致）
    crs_pts = build_points(cum_rows, "CRS")
    comp = weighted_mean_series(crs_pts, MAX_FORWARD_FILL_MIN)
    stratum = "high" if (not math.isnan(comp)
                         and comp > COMPLIANCE_STRATUM_THRESHOLD) else "low"

    # ── FR-05.1 斜率 ──
    slope_dp = ANZ.sliding_slopes(series, "dp")
    slope_mp = ANZ.sliding_slopes(series, "mp")

    # ── FR-05.2 CUSUM ──
    cusum_dp = ANZ.cusum_track(series, "dp")
    cusum_mp = ANZ.cusum_track(series, "mp")

    # ── FR-06 G0-G3 防抖引擎（真·连续分钟口径） ──
    eng = ANZ.DebounceGradeEngine(stratum=stratum)
    # 最近连续段（与最新点连续），喂给引擎求当前级；斜率方向用 6h 窗
    slope6 = slope_dp.get(6.0, {})
    slope6v = slope6.get("beta")
    dp_slope_up = (slope6v is not None and slope6v > 0)
    events = []
    cur_grade = ANZ.G0
    for r in series:
        g, ev = eng.feed(r["ts"], r["dp"], r["mp"],
                         dp_tat_24h=dp_tat_24h, mp_tat_24h=mp_tat_24h,
                         dp_slope_up=dp_slope_up)
        if ev:
            events.append({"ts": r["ts"], "event": ev,
                           "grade": g,
                           "grade_label": ANZ.GRADE_LABELS[g],
                           "iso": datetime.fromtimestamp(
                               r["ts"] / 1000, tz=timezone.utc).isoformat()})
        cur_grade = g

    return {
        "device": device_id,
        "hours": hours,
        "source": "metrics_1min" if series is None else "injected",
        "grade": {
            "level": cur_grade,
            "label": ANZ.GRADE_LABELS[cur_grade],
            "color": ANZ.GRADE_COLOR[cur_grade],
            "stratum": stratum,
            "compliance_mean": round(comp, 1) if not math.isnan(comp) else None,
            "dp_tat_24h": round(dp_tat_24h, 2),
            "mp_tat_24h": round(mp_tat_24h, 2),
            "dp_sustain_min": eng.dp_sustain,
            "mp_sustain_min": eng.mp_sustain,
            "events": events[-12:],  # 最近 12 条升降级事件
        },
        "slope": {"dp": slope_dp, "mp": slope_mp},
        "cusum": {"dp": cusum_dp, "mp": cusum_mp},
    }


# ════════════════════ 核心逻辑（REST + WS 共用） ════════════════════

def _get_overview_data(device_id: str = DEVICE_ID, hours: float = DEFAULT_WINDOW_HOURS) -> dict:
    """
    总览数据核心逻辑（同步），REST 和 WebSocket 共用。
    优先从 metrics_1min 取聚合结果（快），回退到实时计算。
    """
    db = get_database()
    # 取最新分钟（metrics_1min 已建索引，毫秒级；首次未聚合则回退 raw 全表扫描）
    latest_ts = get_latest_minute_ts(db, device_id)
    if latest_ts == 0:
        _, latest_ts = get_time_range(db, device_id)
    if latest_ts == 0:
        return {"error": "no_data", "device": device_id}

    start_ts = latest_ts - int(hours * 3600 * 1000)

    # 获取当前通气模式（work_mode 集合仅在模式切换时写入，全局最新记录即当前模式）。
    # 注意：不能以聚合分钟 latest_ts 为锚点——模式切换时间戳可能晚于最新聚合分钟，
    # 用 at_ts 过滤会漏掉最新切换、回退到旧模式（实测 PCV 实为 SIMV-PC）。
    work_mode = get_current_work_mode(db, device_id)

    agg_docs = list(db[COLL_1MIN].find(
        {"deviceId": device_id, "minute": {"$gte": start_ts, "$lte": latest_ts}},
        {"_id": 0},
    ).sort("minute", 1))

    if agg_docs:
        vent_docs = [d for d in agg_docs if d.get("is_ventilating")]
        if not vent_docs:
            vent_docs = agg_docs

        dp_vals = [d["dp_mean"] for d in vent_docs if d.get("dp_mean") is not None]
        mp_vals = [d["mp_mean"] for d in vent_docs if d.get("mp_mean") is not None]

        dp_max_val = max((d["dp_max"] for d in vent_docs if d.get("dp_max") is not None), default=0)
        mp_max_val = max((d["mp_max"] for d in vent_docs if d.get("mp_max") is not None), default=0)

        dp_over_pct = sum(d.get("dp_over_pct", 0) for d in vent_docs) / len(vent_docs) if vent_docs else 0
        mp_over_pct = sum(d.get("mp_over_pct", 0) for d in vent_docs) / len(vent_docs) if vent_docs else 0

        # 「当前」值 = 最近一次有效通气（排除脏值），而非末分钟字面量
        dp_current, mp_current = _latest_valid_ventilation(vent_docs)

        # 「通气全程」滚动累计存于每一条「通气」分钟文档；取最新一条通气分钟，
        # 避免末分钟为待机(累计写0)或窗口截断导致取到 0.0。
        last_doc = vent_docs[-1] if vent_docs else agg_docs[-1]
        vent_min = last_doc.get("vent_duration_min", 0)
        risk = last_doc.get("risk_level", 1)
        cum_risk = last_doc.get("cumulative_risk_level", 1)

        cum_state = {
            "cum_over_minutes": {
                "dp": last_doc.get("cum_dp_over_min", 0),
                # 修正：此前 mp17 误读 cum_mp_over_min_18（当时该字段不存在），
                #       导致 MP PTA 与窗口口径对不上（27.9% vs 81.7%）。
                "mp17": last_doc.get("cum_mp_over_min_17", 0),
                "mp18": last_doc.get("cum_mp_over_min_18", 0),
                "mp20": last_doc.get("cum_mp_over_min_20", 0),
            },
            "compliance_mean": last_doc.get("compliance_mean"),
            "energy_j": last_doc.get("cum_energy", 0),
            "cum_auc_above": {
                "dp": last_doc.get("cum_dp_auc_above", 0),
                "mp17": last_doc.get("cum_mp_auc_above_17", 0),
                "mp18": last_doc.get("cum_mp_auc_above_18", 0),
                "mp20": last_doc.get("cum_mp_auc_above_20", 0),
            },
            "vent_min": vent_min,
            "cum_risk": cum_risk,
            "gap_min": last_doc.get("cum_gap_min", 0) or 0,
            "n_gaps": last_doc.get("cum_n_gaps", 0) or 0,
            "dp_source": last_doc.get("dp_source", "none"),
            "mp_source": last_doc.get("mp_source", "none"),
            # 24h 滚动窗口口径（URS FR-04 / G0~G3 判据所需）
            "window": _window_cumulative(db, device_id, start_ts, latest_ts, hours),
        }

        result = {
            "device": device_id,
            "source": "metrics_1min",
            "work_mode": work_mode,
            "risk_level": risk,
            "risk_label": RISK_LABELS.get(risk, "L1 正常"),
            "risk_level_instant": last_doc.get("risk_level_instant", 1),
            "cumulative_risk_level": cum_risk,
            "dp": {
                "current": round(dp_current, 1) if dp_current is not None else None,
                "max": round(dp_max_val, 1),
                "mean": round(sum(dp_vals) / len(dp_vals), 1) if dp_vals else None,
                "threshold": DP_THRESHOLD,
                "over_pct": round(dp_over_pct, 1),
            },
            "mp": {
                "current": round(mp_current, 2) if mp_current is not None else None,
                "max": round(mp_max_val, 2),
                "mean": round(sum(mp_vals) / len(mp_vals), 2) if mp_vals else None,
                "threshold": MP_THRESHOLD,
                "over_pct": round(mp_over_pct, 1),
            },
            "cumulative": _cumulative_block(cum_state),
            "vent_minutes": len(vent_docs),
            "total_minutes": len(agg_docs),
        }

    else:
        # 回退到实时计算（仅当 metrics_1min 尚无聚合数据，如首次运行/未启动聚合守护进程）
        rows = collect_raw(db, device_id, start_ts, latest_ts)
        if not rows:
            return {"error": "no_data_window", "device": device_id}

        enrich_rows(rows)
        vent_rows, _ = filter_ventilated(rows)
        use_rows = vent_rows if vent_rows else rows
        exposure = compute_exposure(use_rows)

        cum_risk = exposure["cumulative_risk_level"]
        risk = max(exposure["risk_level"], cum_risk)
        # 有效通气时长：按真实时间跨度积分（含 ≤4h 前向填充），
        # 而非旧的「点数 × 4 秒」硬编码（实测会把 8h 通气算成 0.1 min）。
        vent_min = exposure.get("vent_minutes") or 0.0

        cum_state = {
            "cum_over_minutes": exposure["cum_over_minutes"],
            "compliance_mean": exposure.get("compliance_mean"),
            "energy_j": exposure["cum_energy_j"],
            "cum_auc_above": exposure["cum_auc_above"],
            "vent_min": vent_min,
            "cum_risk": cum_risk,
            "gap_min": exposure.get("gap_minutes", 0) or 0,
            "n_gaps": exposure.get("n_gaps", 0) or 0,
            "dp_source": exposure.get("dp_source", "none"),
            "mp_source": exposure.get("mp_source", "none"),
            "window": _window_cumulative(db, device_id, start_ts, latest_ts, hours),
        }

        result = {
            "device": device_id,
            "source": "realtime",
            "work_mode": work_mode,
            "risk_level": risk,
            "risk_label": RISK_LABELS.get(risk, "L1 正常"),
            "risk_level_instant": exposure["risk_level"],
            "cumulative_risk_level": cum_risk,
            "dp": {
                "current": round(use_rows[-1]["dP"], 1) if use_rows and not math.isnan(use_rows[-1].get("dP", float("nan"))) else None,
                "max": round(exposure["dp"]["max"], 1) if not math.isnan(exposure["dp"]["max"]) else None,
                "mean": round(exposure["dp"]["mean"], 1) if not math.isnan(exposure["dp"]["mean"]) else None,
                "threshold": DP_THRESHOLD,
                "over_pct": round(exposure["dp"]["over_pct"], 1),
            },
            "mp": {
                "current": round(use_rows[-1]["MP"], 2) if use_rows and not math.isnan(use_rows[-1].get("MP", float("nan"))) else None,
                "max": round(exposure["mp"]["max"], 2) if not math.isnan(exposure["mp"]["max"]) else None,
                "mean": round(exposure["mp"]["mean"], 2) if not math.isnan(exposure["mp"]["mean"]) else None,
                "threshold": MP_THRESHOLD,
                "over_pct": round(exposure["mp"]["over_pct"], 1),
            },
            "cumulative": _cumulative_block(cum_state),
            "vent_minutes": int(vent_min),
            "total_minutes": int(exposure.get("vent_minutes") or 0),
        }

    # ── 实时当前值（方案A）：原始批次到达即计算，不等聚合分钟关门 ──
    live = compute_live_current(db, device_id)
    result["live"] = live
    # 修正 risk_level_instant 语义：真正的「瞬时单值风险」（此前误用了窗口暴露风险）。
    # 顶部等级条应随当前读数秒级回落，而非因近期历史高值长期停留 L4。
    if live.get("valid"):
        result["risk_level_instant"] = live.get("risk_level", result.get("risk_level_instant", 1))
    if live.get("valid") and result.get("dp") and result.get("mp"):
        result["dp"]["current"] = live["dp"]
        result["mp"]["current"] = live["mp"]
        # 实时读数降级为动态口径时，前端在数值旁打 *Dyn*
        result["dp"]["source"] = live.get("dp_source", "none")
        result["mp"]["source"] = live.get("mp_source", "none")
    # ── 通气参数快照（方案A 修复：前端此前为写死占位值）──
    result["snapshot"] = compute_snapshot(db, device_id)
    return _clean_nan(result)


async def _ws_push_loop():
    """后台任务：每2秒向 WebSocket 客户端推送最新总览数据（含实时当前值）"""
    logger.info("WebSocket 推送循环启动 (2s 间隔)")
    while True:
        try:
            if manager.active:
                data = await asyncio.get_event_loop().run_in_executor(
                    None, _get_overview_data
                )
                if data and "error" not in data:
                    await manager.broadcast({"type": "overview", "data": data})
        except Exception as e:
            logger.error(f"WS push 异常: {e}")
        await asyncio.sleep(2)


@app.on_event("startup")
async def _startup():
    asyncio.create_task(_ws_push_loop())


# ════════════════════ REST 端点 ════════════════════

@app.get("/api/health")
async def health_check():
    db = get_database()
    try:
        old_ts, new_ts = get_time_range(db, DEVICE_ID)
        if new_ts == 0:
            return {"status": "warn", "message": "无数据", "device": DEVICE_ID}
        return {
            "status": "ok",
            "device": DEVICE_ID,
            "mongo": MONGO_URI,
            "db": MONGO_DB,
            "data_range": {
                "oldest": datetime.fromtimestamp(old_ts / 1000, tz=timezone.utc).isoformat(),
                "newest": datetime.fromtimestamp(new_ts / 1000, tz=timezone.utc).isoformat(),
            },
            "thresholds": {"dp": DP_THRESHOLD, "mp": MP_THRESHOLD},
            "cumulative_thresholds": CUM_THRESHOLDS,
            "compliance_stratum_threshold": COMPLIANCE_STRATUM_THRESHOLD,
            "ws_connected": len(manager.active),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/overview")
async def get_overview(
    deviceId: str = Query(default=DEVICE_ID),
    hours: float = Query(default=DEFAULT_WINDOW_HOURS, ge=0.1, le=168),
):
    """总览页数据：风险评级 + ΔP/MP 仪表盘值 + 累积量"""
    data = await asyncio.get_event_loop().run_in_executor(
        None, _get_overview_data, deviceId, hours
    )
    if "error" in data:
        if data["error"] == "no_data":
            raise HTTPException(status_code=404, detail="无数据")
        raise HTTPException(status_code=404, detail="该时间窗口无数据")
    data["window_hours"] = hours
    return data


@app.get("/api/dp/trend")
async def get_dp_trend(
    deviceId: str = Query(default=DEVICE_ID),
    hours: float = Query(default=DEFAULT_WINDOW_HOURS, ge=0.1, le=168),
    points: int = Query(default=120, ge=10, le=2000),
):
    """ΔP 时间序列（优先读 metrics_1min，秒级返回）"""
    db = get_database()
    latest_ts = get_latest_minute_ts(db, deviceId)
    if latest_ts == 0:
        _, latest_ts = get_time_range(db, deviceId)
    start_ts = latest_ts - int(hours * 3600 * 1000)

    series = _trend_from_1min(db, deviceId, start_ts, latest_ts, "dp_mean", DP_THRESHOLD, points, 1) \
        or _trend_from_raw(db, deviceId, start_ts, latest_ts, "dP", DP_THRESHOLD, points, 1)
    return {"device": deviceId, "points": len(series), "series": series}


@app.get("/api/mp/trend")
async def get_mp_trend(
    deviceId: str = Query(default=DEVICE_ID),
    hours: float = Query(default=DEFAULT_WINDOW_HOURS, ge=0.1, le=168),
    points: int = Query(default=120, ge=10, le=2000),
):
    """MP 时间序列（优先读 metrics_1min，秒级返回）"""
    db = get_database()
    latest_ts = get_latest_minute_ts(db, deviceId)
    if latest_ts == 0:
        _, latest_ts = get_time_range(db, deviceId)
    start_ts = latest_ts - int(hours * 3600 * 1000)

    series = _trend_from_1min(db, deviceId, start_ts, latest_ts, "mp_mean", MP_THRESHOLD, points, 2) \
        or _trend_from_raw(db, deviceId, start_ts, latest_ts, "MP", MP_THRESHOLD, points, 2)
    return {"device": deviceId, "points": len(series), "series": series}


@app.get("/api/risk-map")
async def get_risk_map(
    deviceId: str = Query(default=DEVICE_ID),
    hours: float = Query(default=DEFAULT_WINDOW_HOURS, ge=0.1, le=168),
    points: int = Query(default=300, ge=10, le=5000),
):
    """ΔP-MP 二维散点数据（优先读 metrics_1min，秒级返回）"""
    db = get_database()
    latest_ts = get_latest_minute_ts(db, deviceId)
    if latest_ts == 0:
        _, latest_ts = get_time_range(db, deviceId)
    start_ts = latest_ts - int(hours * 3600 * 1000)

    pts = _riskmap_from_1min(db, deviceId, start_ts, latest_ts, points) \
        or _riskmap_from_raw(db, deviceId, start_ts, latest_ts, points)
    return {
        "device": deviceId,
        "points": len(pts),
        "thresholds": {"dp": DP_THRESHOLD, "mp": MP_THRESHOLD},
        "series": pts,
    }


@app.get("/api/analysis")
async def get_analysis(
    deviceId: str = Query(default=DEVICE_ID),
    hours: float = Query(default=24, ge=0.1, le=168),
):
    """URS FR-05/FR-06 分析：滑动斜率 + CUSUM 变化点 + G0-G3 防抖总评。

    URS 引擎作用于连续 1-min 通气序列；当前库内真实数据稀疏时 slope 各档
    返回 insufficient、CUSUM 无变化点、grade=G0，属预期（待真实设备连续数据验证）。
    """
    try:
        data = _compute_analysis(deviceId, hours)
    except Exception as e:
        logger.exception("analysis failed")
        return JSONResponse(status_code=500, content={"error": str(e)})
    return _clean_nan(data)


# ── 累积暴露监测：按顺应性分层的结论文案（简略） ──
_EXPOSURE_TEXT = {
    # level: (风险提示, 建议)
    0: ("暴露处于本层安全范围", "维持当前参数，常规监测"),
    1: ("接近本层安全上限", "关注趋势，复核 VT / RR / PEEP"),
    2: ("累积暴露已超本层 L3", "建议下调 VT 或 RR，评估 PEEP 与驱动压来源"),
    3: ("累积暴露已达本层 L4", "尽快个体化调整，评估肺保护通气策略"),
}


def _exposure_stratum_and_limits(stratum: str, param: str) -> dict:
    """按顺应性分层给出该参数的阈值与 L3/L4 参照（复用 config 既有口径）。"""
    if param == "dp":
        return {
            "instant_thr": DP_THRESHOLD,
            "tat_l3_h": CUM_DP_OVER_HOURS_L3,
            "tat_l4_h": CUM_DP_OVER_HOURS_L4,
            "auc_unit": "cmH₂O·h",
            "value_unit": "cmH₂O",
        }
    # MP：阈值与 L3/L4 随分层变化
    if stratum == "high":
        return {
            "instant_thr": MP_HIGH_STRATUM_THRESHOLD,
            "tat_l3_h": CUM_MP_OVER_HOURS_L3_HIGH,
            "tat_l4_h": CUM_MP_OVER_HOURS_L4_HIGH,
            "auc_unit": "J·h/min",
            "value_unit": "J/min",
        }
    return {
        "instant_thr": MP_LOW_STRATUM_THRESHOLD,
        "tat_l3_h": CUM_MP_OVER_HOURS_L3_LOW,
        "tat_l4_h": CUM_MP_OVER_HOURS_L4_LOW,
        "auc_unit": "J·h/min",
        "value_unit": "J/min",
    }


def _compute_exposure_summary(device_id: str, param: str, hours: float) -> dict:
    """总览「累积暴露监测」卡数据（URS 07-1/2 重构版）。

    返回 4 段：
      series   —— 窗口内分钟均值序列（左图用，含 over 标记与真实时间戳）
      metrics  —— TAT / AUC / PTA / peak / mean（累积暴露指标）
      limits   —— 本层阈值与 L3/L4 参照（供前端进度条）
      conclusion—— 分层风险结论（风险提示 / 建议 / 依据，简略）

    结论判级 = max(累积维度(TAT 对 L3/L4), 瞬时维度(峰值对阈值))，与 G0-G3 同源。
    """
    db = get_database()
    latest_ts = get_latest_minute_ts(db, device_id)
    if latest_ts == 0:
        _, latest_ts = get_time_range(db, device_id)
    if latest_ts == 0:
        return {"error": "no_data", "device": device_id}

    start_ts = latest_ts - int(hours * 3600 * 1000)
    docs = list(db[COLL_1MIN].find(
        {"deviceId": device_id, "minute": {"$gte": start_ts, "$lte": latest_ts},
         "dp_mean": {"$ne": None}, "mp_mean": {"$ne": None}},
        {"_id": 0, "minute": 1, "dp_mean": 1, "mp_mean": 1, "vt_mean": 1},
    ).sort("minute", 1))

    win = _window_cumulative(db, device_id, start_ts, latest_ts, hours)
    if not win:
        return {"error": "no_data", "device": device_id}

    stratum = win.get("compliance_stratum") or "low"
    limits = _exposure_stratum_and_limits(stratum, param)

    # ── 指标 ──
    if param == "dp":
        tat_h = win.get("dp_tat_hours") or 0.0
        auc = win.get("dp_auc_h") or 0.0
        pta = win.get("dp_pta") or 0.0
        vals = [d["dp_mean"] for d in docs if d.get("dp_mean") is not None]
    else:
        mp_key = "mp18" if stratum == "high" else "mp20"
        tat_h = (win.get("mp_tat_hours") or {}).get(mp_key, 0.0)
        auc = (win.get("mp_auc_h") or {}).get(mp_key, 0.0)
        pta = win.get("mp_pta") or 0.0
        vals = [d["mp_mean"] for d in docs if d.get("mp_mean") is not None]

    peak = round(max(vals), 2) if vals else None
    mean = round(sum(vals) / len(vals), 2) if vals else None

    # ── 序列（供左图：分钟均值 + over 标记） ──
    thr = limits["instant_thr"]
    series_out = [{"ts": d["minute"],
                   "v": round(d["dp_mean"] if param == "dp" else d["mp_mean"], 2),
                   "over": (d["dp_mean"] if param == "dp" else d["mp_mean"]) >= thr}
                  for d in docs]

    # ── 结论判级：累积维度（TAT 对 L3/L4）与瞬时维度（峰值对阈值）取最高 ──
    l3, l4 = limits["tat_l3_h"], limits["tat_l4_h"]
    if tat_h >= l4:
        level = 3
    elif tat_h >= l3:
        level = 2
    elif tat_h > 0 or (peak is not None and peak >= thr):
        level = 1
    else:
        level = 0

    hint, advice = _EXPOSURE_TEXT[level]
    # 依据（简略、只列关键量）
    basis_bits = [f"TAT {tat_h:.1f}h/本层L3 {l3:g}h"]
    if peak is not None:
        basis_bits.append(f"峰值 {peak:g}≥阈{thr:g}" if peak >= thr else f"峰值 {peak:g}")
    basis_bits.append(f"PTA {pta:.0f}%")
    basis = "；".join(basis_bits)

    return {
        "device": device_id,
        "param": param,
        "hours": hours,
        "stratum": stratum,
        "compliance_mean": win.get("compliance_mean"),
        "metrics": {
            "tat_h": round(tat_h, 2),
            "auc": round(auc, 2),
            "pta": round(pta, 1),
            "peak": peak,
            "mean": mean,
        },
        "limits": limits,
        "conclusion": {
            "level": level,
            "label": ["L1 正常", "L2 关注", "L3 偏高", "L4 危险"][level],
            "hint": hint,
            "advice": advice,
            "basis": basis,
        },
        "series": series_out,
        "dcr": win.get("dcr"),
    }


@app.get("/api/exposure-summary")
async def get_exposure_summary(
    deviceId: str = Query(default=DEVICE_ID),
    param: str = Query(default="dp", pattern="^(dp|mp)$"),
    hours: float = Query(default=6, ge=0.1, le=168),
):
    """总览累积暴露监测卡：分层阈值 + 累积指标 + 风险结论（后端按顺应性分层判定）。"""
    try:
        data = await asyncio.get_event_loop().run_in_executor(
            None, _compute_exposure_summary, deviceId, param, hours)
    except Exception as e:
        logger.exception("exposure-summary failed")
        return JSONResponse(status_code=500, content={"error": str(e)})
    if data.get("error") == "no_data":
        raise HTTPException(status_code=404, detail="该时间窗口无数据")
    return _clean_nan(data)


def _compute_shift_summary(device_id: str, hours: float) -> dict:
    """URS 07-4 交接班摘要：汇总本班次（8/12/24h）力学暴露与干预记录。

    口径一律复用既有函数，避免与 G0-G3 判级、24h 滚动窗口打架：
      - 最高暴露 / 超标总时长 / AUC 增量 → _window_cumulative（真实时间积分）
      - 趋势斜率方向 → analyzer.sliding_slopes（1/6/24h β）
      - 预警干预记录 → cockpit_alerts（含确认状态）
    """
    db = get_database()
    latest_ts = get_latest_minute_ts(db, device_id)
    if latest_ts == 0:
        _, latest_ts = get_time_range(db, device_id)
    if latest_ts == 0:
        return {"error": "no_data", "device": device_id}

    start_ts = latest_ts - int(hours * 3600 * 1000)
    win = _window_cumulative(db, device_id, start_ts, latest_ts, hours)

    # 趋势斜率方向（复用 FR-05 引擎）
    docs = list(db[COLL_1MIN].find(
        {"deviceId": device_id, "minute": {"$gte": start_ts, "$lte": latest_ts},
         "dp_mean": {"$ne": None}, "mp_mean": {"$ne": None}},
        {"_id": 0, "minute": 1, "dp_mean": 1, "mp_mean": 1, "vt_mean": 1,
         "is_ventilating": 1},
    ).sort("minute", 1))
    series = _minute_docs_to_series(docs)
    slope_dp = ANZ.sliding_slopes(series, "dp") if series else {}
    slope_mp = ANZ.sliding_slopes(series, "mp") if series else {}

    def _dir(slopes) -> str:
        """斜率方向：优先 6h 窗，退化取 24h/1h。"""
        for w in (6.0, 24.0, 1.0):
            s = slopes.get(w, {})
            b = s.get("beta")
            if b is None:
                continue
            if b > 0.01:
                return "上升"
            if b < -0.01:
                return "下降"
            return "平稳"
        return "数据不足"

    # 预警干预记录（本班次窗口内）
    alerts = list(db[COLL_ALERTS].find(
        {"deviceId": device_id, "ts": {"$gte": start_ts, "$lte": latest_ts}},
    ).sort("ts", -1).limit(50))
    alert_list = [_serialize_alert(a) for a in alerts]

    stratum = (win or {}).get("compliance_stratum")
    return {
        "device": device_id,
        "hours": hours,
        "window_start_iso": datetime.fromtimestamp(
            start_ts / 1000, tz=timezone.utc).isoformat(),
        "window_end_iso": datetime.fromtimestamp(
            latest_ts / 1000, tz=timezone.utc).isoformat(),
        "peak": {
            "dp_max": round(max((r["dp"] for r in series if r["dp"] is not None),
                                default=float("nan")), 1),
            "mp_max": round(max((r["mp"] for r in series if r["mp"] is not None),
                                default=float("nan")), 2),
        },
        "tat": {
            "dp_hours": (win or {}).get("dp_tat_hours"),
            "mp_hours": (win or {}).get("mp_tat_hours"),
        },
        "auc": {
            "dp_auc_h": (win or {}).get("dp_auc_h"),
            "mp_auc_h": (win or {}).get("mp_auc_h"),
        },
        "vent_hours": (win or {}).get("vent_hours"),
        "gap_minutes": (win or {}).get("gap_minutes"),
        "dcr": (win or {}).get("dcr"),
        "compliance_mean": (win or {}).get("compliance_mean"),
        "compliance_stratum": stratum,
        "slope_direction": {"dp": _dir(slope_dp), "mp": _dir(slope_mp)},
        "slope_detail": {"dp": slope_dp, "mp": slope_mp},
        "alerts": {
            "count": len(alert_list),
            "acknowledged": sum(1 for a in alert_list if a.get("acknowledged")),
            "items": alert_list[:10],
        },
        "thresholds": {"dp": DP_THRESHOLD, "mp": MP_THRESHOLD},
    }


@app.get("/api/shift-summary")
async def get_shift_summary(
    deviceId: str = Query(default=DEVICE_ID),
    hours: float = Query(default=8, ge=0.5, le=168),
):
    """URS 07-4：交接班摘要（8/12/24h 临床力学交接单数据）。

    服务端计算以保证与 G0-G3 防抖判级、24h 滚动窗口口径一致；前端只渲染与导出。
    """
    try:
        data = _compute_shift_summary(deviceId, hours)
    except Exception as e:
        logger.exception("shift-summary failed")
        return JSONResponse(status_code=500, content={"error": str(e)})
    return _clean_nan(data)


def _serialize_alert(doc: dict) -> dict:
    """把 MongoDB 文档转为前端可消费的 JSON 结构。

    - `_id`(ObjectId) → `id`(str)：前端据此唯一定位并确认某条预警
    - 补齐状态字段：本服务上线前写入的历史预警没有 active / acknowledged 字段，
      在此处按「活动中」兜底，避免旧数据永远无法确认。
    """
    d = dict(doc)
    d["id"] = str(d.pop("_id", ""))
    d["active"] = bool(d.get("active", True))
    d["acknowledged"] = bool(d.get("acknowledged", False))
    d.setdefault("acknowledged_by", None)
    d.setdefault("acknowledged_at", None)
    d.setdefault("acknowledged_at_iso", None)
    return _clean_nan(d)


@app.get("/api/alerts")
async def get_alerts(
    deviceId: str = Query(default=DEVICE_ID),
    hours: float = Query(default=24, ge=0.1, le=168),
    limit: int = Query(default=100, ge=1, le=500),
):
    """预警事件列表（含确认状态，按时间倒序）"""
    db = get_database()
    _, latest_ts = get_time_range(db, deviceId)
    start_ts = latest_ts - int(hours * 3600 * 1000)

    docs = list(db[COLL_ALERTS].find(
        {"deviceId": deviceId, "ts": {"$gte": start_ts, "$lte": latest_ts}},
    ).sort("ts", -1).limit(limit))

    alerts = [_serialize_alert(d) for d in docs]
    active_n = sum(1 for a in alerts if a["active"])
    return {
        "device": deviceId,
        "count": len(alerts),
        "active_count": active_n,
        "alerts": alerts,
    }


def _ack_alert_doc(db, doc: dict, operator: str) -> dict:
    """把单条预警置为「已确认」，返回更新后的文档。"""
    now_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
    update = {
        "active": False,
        "acknowledged": True,
        "acknowledged_by": operator or "操作员",
        "acknowledged_at": now_ms,
        "acknowledged_at_iso": datetime.fromtimestamp(
            now_ms / 1000, tz=timezone.utc).isoformat(),
    }
    db[COLL_ALERTS].update_one({"_id": doc["_id"]}, {"$set": update})
    return _serialize_alert({**doc, **update})


@app.post("/api/alerts/{alert_id}/ack")
async def ack_alert(
    alert_id: str,
    deviceId: str = Query(default=DEVICE_ID),
    payload: dict = Body(default=None),
):
    """确认单条预警（持久化到 MongoDB，刷新页面后依然是「已确认」）。

    body: {"operator": "张医师"}  —— 可选，缺省为「操作员」
    """
    db = get_database()
    try:
        oid = ObjectId(alert_id)
    except (InvalidId, TypeError):
        raise HTTPException(status_code=400, detail=f"非法的预警 ID: {alert_id}")

    doc = db[COLL_ALERTS].find_one({"_id": oid})
    if not doc:
        raise HTTPException(status_code=404, detail="预警不存在")
    if doc.get("deviceId") != deviceId:
        raise HTTPException(status_code=404, detail="预警不属于该设备")

    operator = ((payload or {}).get("operator") or "操作员").strip() or "操作员"
    updated = _ack_alert_doc(db, doc, operator)
    logger.info(f"预警确认: {alert_id} by {operator}")
    return {"ok": True, "alert": updated}


@app.post("/api/alerts/ack-all")
async def ack_all_alerts(
    deviceId: str = Query(default=DEVICE_ID),
    hours: float = Query(default=24, ge=0.1, le=168),
    payload: dict = Body(default=None),
):
    """批量确认当前窗口内全部「活动中」预警。"""
    db = get_database()
    _, latest_ts = get_time_range(db, deviceId)
    start_ts = latest_ts - int(hours * 3600 * 1000)

    operator = ((payload or {}).get("operator") or "操作员").strip() or "操作员"
    pending = list(db[COLL_ALERTS].find({
        "deviceId": deviceId,
        "ts": {"$gte": start_ts, "$lte": latest_ts},
        "active": {"$ne": False},
    }))

    updated = [_ack_alert_doc(db, d, operator) for d in pending]
    logger.info(f"批量确认 {len(updated)} 条预警 by {operator}")
    return {"ok": True, "acknowledged": len(updated), "operator": operator,
            "alerts": updated}


@app.get("/api/metrics/1min")
async def get_metrics_1min(
    deviceId: str = Query(default=DEVICE_ID),
    hours: float = Query(default=DEFAULT_WINDOW_HOURS, ge=0.1, le=168),
    limit: int = Query(default=200, ge=1, le=5000),
):
    """1分钟聚合数据明细"""
    db = get_database()
    _, latest_ts = get_time_range(db, deviceId)
    start_ts = latest_ts - int(hours * 3600 * 1000)

    docs = list(db[COLL_1MIN].find(
        {"deviceId": deviceId, "minute": {"$gte": start_ts, "$lte": latest_ts}},
        {"_id": 0},
    ).sort("minute", -1).limit(limit))

    return {"device": deviceId, "count": len(docs), "minutes": docs}


# ════════════════════ WebSocket 端点 ════════════════════

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await manager.connect(ws)
    try:
        # 连接时立即推一次
        data = await asyncio.get_event_loop().run_in_executor(
            None, _get_overview_data
        )
        if data and "error" not in data:
            await ws.send_text(json.dumps(
                {"type": "overview", "data": data},
                ensure_ascii=False, default=str
            ))
        # 保持连接，接收心跳
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(ws)
    except Exception as e:
        logger.error(f"WS 异常: {e}")
        manager.disconnect(ws)


# ════════════════════ 前端 HTML ════════════════════

@app.get("/", response_class=HTMLResponse)
async def serve_frontend():
    """托管前端驾驶舱 HTML"""
    if os.path.exists(_FRONTEND_HTML):
        return FileResponse(_FRONTEND_HTML, media_type="text/html")
    return HTMLResponse(
        "<h1>前端文件未找到</h1>"
        "<p>请运行生成脚本: python outputs/scripts/gen_frontend.py</p>",
        status_code=404,
    )
