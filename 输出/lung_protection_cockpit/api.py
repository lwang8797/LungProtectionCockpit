# -*- coding: utf-8 -*-
"""
api.py - M3+M4: FastAPI REST + WebSocket 实时服务

REST 端点:
  GET  /api/health            - 健康检查
  GET  /api/overview           - 总览仪表盘
  GET  /api/dp/trend           - ΔP 时间序列
  GET  /api/mp/trend           - MP 时间序列
  GET  /api/risk-map           - 二维风险图散点
  GET  /api/alerts             - 预警事件列表
  GET  /api/metrics/1min       - 1分钟聚合数据

WebSocket:
  WS   /ws                     - 实时推送总览数据（每5秒）

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

from fastapi import FastAPI, Query, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from .config import (
    MONGO_URI, MONGO_DB, COLL_RAW, COLL_1MIN, COLL_ALERTS,
    DEVICE_ID, DP_THRESHOLD, MP_THRESHOLD, RISK_LABELS,
    DEFAULT_WINDOW_HOURS,
    COMPLIANCE_STRATUM_THRESHOLD,
    CUM_DP_OVER_HOURS_L3, CUM_DP_OVER_HOURS_L4,
    MP_HIGH_STRATUM_THRESHOLD, MP_LOW_STRATUM_THRESHOLD,
    CUM_MP_OVER_HOURS_L3_HIGH, CUM_MP_OVER_HOURS_L4_HIGH,
    CUM_MP_OVER_HOURS_L3_LOW, CUM_MP_OVER_HOURS_L4_LOW,
)
from .collector import get_db, get_time_range, collect_raw, get_current_work_mode
from .calculator import enrich_rows, filter_ventilated, compute_exposure, build_risk_map_points

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

    return {
        "dp_over_hours": round(dp_over_hours, 2),
        "mp_over_hours_high": round(mp_over_hours_high, 2),
        "mp_over_hours_low": round(mp_over_hours_low, 2),
        "mp_over_hours": round(mp_over_hours, 2),
        "compliance_stratum": stratum,
        "compliance_mean": round(comp, 1) if s.get("compliance_mean") else None,
        "energy_kj": round((s.get("energy_j") or 0) / 1000.0, 2),
        "dp_auc_above": round(s["cum_auc_above"]["dp"] or 0, 1),
        "mp_auc_above_17": round(s["cum_auc_above"]["mp17"] or 0, 1),
        "mp_auc_above_18": round(s["cum_auc_above"]["mp18"] or 0, 1),
        "mp_auc_above_20": round(s["cum_auc_above"]["mp20"] or 0, 1),
        "vent_duration_min": round(s.get("vent_min") or 0, 1),
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


# ════════════════════ 核心逻辑（REST + WS 共用） ════════════════════

def _get_overview_data(device_id: str = DEVICE_ID, hours: float = DEFAULT_WINDOW_HOURS) -> dict:
    """
    总览数据核心逻辑（同步），REST 和 WebSocket 共用。
    优先从 metrics_1min 取聚合结果（快），回退到实时计算。
    """
    db = get_database()
    _, latest_ts = get_time_range(db, device_id)
    if latest_ts == 0:
        return {"error": "no_data", "device": device_id}

    start_ts = latest_ts - int(hours * 3600 * 1000)

    # 获取当前通气模式（work_mode 集合仅在变化时写入）
    work_mode = get_current_work_mode(db, device_id, latest_ts)

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

        last_doc = agg_docs[-1]
        vent_min = last_doc.get("vent_duration_min", 0)
        risk = last_doc.get("risk_level", 1)
        cum_risk = last_doc.get("cumulative_risk_level", 1)

        cum_state = {
            "cum_over_minutes": {
                "dp": last_doc.get("cum_dp_over_min", 0),
                "mp17": last_doc.get("cum_mp_over_min_18", 0),
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
        }

        return {
            "device": device_id,
            "source": "metrics_1min",
            "work_mode": work_mode,
            "risk_level": risk,
            "risk_label": RISK_LABELS.get(risk, "L1 正常"),
            "risk_level_instant": last_doc.get("risk_level_instant", 1),
            "cumulative_risk_level": cum_risk,
            "dp": {
                "current": dp_vals[-1] if dp_vals else None,
                "max": round(dp_max_val, 1),
                "mean": round(sum(dp_vals) / len(dp_vals), 1) if dp_vals else None,
                "threshold": DP_THRESHOLD,
                "over_pct": round(dp_over_pct, 1),
            },
            "mp": {
                "current": mp_vals[-1] if mp_vals else None,
                "max": round(mp_max_val, 2),
                "mean": round(sum(mp_vals) / len(mp_vals), 2) if mp_vals else None,
                "threshold": MP_THRESHOLD,
                "over_pct": round(mp_over_pct, 1),
            },
            "cumulative": _cumulative_block(cum_state),
            "vent_minutes": len(vent_docs),
            "total_minutes": len(agg_docs),
        }

    # 回退到实时计算
    rows = collect_raw(db, device_id, start_ts, latest_ts)
    if not rows:
        return {"error": "no_data_window", "device": device_id}

    enrich_rows(rows)
    vent_rows, _ = filter_ventilated(rows)
    use_rows = vent_rows if vent_rows else rows
    exposure = compute_exposure(use_rows)

    cum_risk = exposure["cumulative_risk_level"]
    risk = max(exposure["risk_level"], cum_risk)
    vent_min = len(use_rows) * 4 / 60

    cum_state = {
        "cum_over_minutes": exposure["cum_over_minutes"],
        "compliance_mean": exposure.get("compliance_mean"),
        "energy_j": exposure["cum_energy_j"],
        "cum_auc_above": exposure["cum_auc_above"],
        "vent_min": vent_min,
        "cum_risk": cum_risk,
    }

    return {
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
        "vent_minutes": int(len(use_rows) * 4 / 60),
        "total_minutes": int(len(rows) * 4 / 60),
    }


async def _ws_push_loop():
    """后台任务：每5秒向 WebSocket 客户端推送最新总览数据"""
    logger.info("WebSocket 推送循环启动 (5s 间隔)")
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
        await asyncio.sleep(5)


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
    """ΔP 时间序列"""
    db = get_database()
    _, latest_ts = get_time_range(db, deviceId)
    start_ts = latest_ts - int(hours * 3600 * 1000)

    rows = collect_raw(db, deviceId, start_ts, latest_ts)
    enrich_rows(rows)

    if len(rows) > points:
        step = len(rows) // points
        rows = rows[::step][:points]

    data = [{
        "ts": r["ts"],
        "dt": r["dt"].isoformat(),
        "value": round(r.get("dP", float("nan")), 1) if not math.isnan(r.get("dP", float("nan"))) else None,
        "threshold": DP_THRESHOLD,
    } for r in rows]

    return {"device": deviceId, "points": len(data), "series": data}


@app.get("/api/mp/trend")
async def get_mp_trend(
    deviceId: str = Query(default=DEVICE_ID),
    hours: float = Query(default=DEFAULT_WINDOW_HOURS, ge=0.1, le=168),
    points: int = Query(default=120, ge=10, le=2000),
):
    """MP 时间序列"""
    db = get_database()
    _, latest_ts = get_time_range(db, deviceId)
    start_ts = latest_ts - int(hours * 3600 * 1000)

    rows = collect_raw(db, deviceId, start_ts, latest_ts)
    enrich_rows(rows)

    if len(rows) > points:
        step = len(rows) // points
        rows = rows[::step][:points]

    data = [{
        "ts": r["ts"],
        "dt": r["dt"].isoformat(),
        "value": round(r.get("MP", float("nan")), 2) if not math.isnan(r.get("MP", float("nan"))) else None,
        "threshold": MP_THRESHOLD,
    } for r in rows]

    return {"device": deviceId, "points": len(data), "series": data}


@app.get("/api/risk-map")
async def get_risk_map(
    deviceId: str = Query(default=DEVICE_ID),
    hours: float = Query(default=DEFAULT_WINDOW_HOURS, ge=0.1, le=168),
    points: int = Query(default=300, ge=10, le=5000),
):
    """ΔP-MP 二维散点数据"""
    db = get_database()
    _, latest_ts = get_time_range(db, deviceId)
    start_ts = latest_ts - int(hours * 3600 * 1000)

    rows = collect_raw(db, deviceId, start_ts, latest_ts)
    enrich_rows(rows)
    vent_rows, _ = filter_ventilated(rows)
    use_rows = vent_rows if vent_rows else rows

    if len(use_rows) > points:
        step = len(use_rows) // points
        use_rows = use_rows[::step][:points]

    pts = build_risk_map_points(use_rows)

    return {
        "device": deviceId,
        "points": len(pts),
        "thresholds": {"dp": DP_THRESHOLD, "mp": MP_THRESHOLD},
        "series": pts,
    }


@app.get("/api/alerts")
async def get_alerts(
    deviceId: str = Query(default=DEVICE_ID),
    hours: float = Query(default=24, ge=0.1, le=168),
    limit: int = Query(default=100, ge=1, le=500),
):
    """预警事件列表"""
    db = get_database()
    _, latest_ts = get_time_range(db, deviceId)
    start_ts = latest_ts - int(hours * 3600 * 1000)

    alerts = list(db[COLL_ALERTS].find(
        {"deviceId": deviceId, "ts": {"$gte": start_ts, "$lte": latest_ts}},
        {"_id": 0},
    ).sort("ts", -1).limit(limit))

    return {"device": deviceId, "count": len(alerts), "alerts": alerts}


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
        "<p>请运行生成脚本: python 输出/scripts/gen_frontend.py</p>",
        status_code=404,
    )
