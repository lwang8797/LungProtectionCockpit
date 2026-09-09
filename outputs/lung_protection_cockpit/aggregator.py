# -*- coding: utf-8 -*-
"""
aggregator.py - M2: 1分钟聚合服务

功能：
  1. 对每分钟的原始 measure_param 数据计算 ΔP/MP
  2. 聚合为分钟级统计（均值/最大/超阈/AUC增量/累积量滚动）
  3. 写入 metrics_1min 集合
  4. 支持 backfill（历史回填）和 continuous（守护进程）两种模式
  5. 生成预警事件写入 cockpit_alerts 集合
"""

import math
import time
import logging
from datetime import datetime, timezone

from .config import (
    MONGO_URI, MONGO_DB, COLL_1MIN, COLL_ALERTS, COLL_RAW,
    DEVICE_ID, DP_THRESHOLD, MP_THRESHOLD, RISK_LABELS,
    COMPLIANCE_STRATUM_THRESHOLD, MAX_FORWARD_FILL_MIN,
    CUM_DP_OVER_HOURS_L3, CUM_DP_OVER_HOURS_L4,
    MP_HIGH_STRATUM_THRESHOLD, MP_LOW_STRATUM_THRESHOLD,
    CUM_MP_OVER_HOURS_L3_HIGH, CUM_MP_OVER_HOURS_L4_HIGH,
    CUM_MP_OVER_HOURS_L3_LOW, CUM_MP_OVER_HOURS_L4_LOW,
)
from .collector import get_db, collect_minute_raw
from .calculator import (
    enrich_rows, filter_ventilated, compute_exposure, classify_cumulative_risk,
    boundary_increment,
)

logger = logging.getLogger("lung_cockpit.aggregator")


def _nan_to_none(v):
    """NaN 转 None（MongoDB 不支持 NaN 存储）"""
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    return v


def _as_float(v) -> float:
    """MongoDB 里缺失/None 的聚合值 → NaN（参与积分时被自动跳过）"""
    if v is None:
        return float("nan")
    try:
        return float(v)
    except (TypeError, ValueError):
        return float("nan")


def _ensure_indexes(db):
    """创建索引"""
    db[COLL_1MIN].create_index(
        [("deviceId", 1), ("minute", -1)],
        name="dev_minute", unique=True,
    )
    db[COLL_ALERTS].create_index(
        [("deviceId", 1), ("ts", -1)],
        name="dev_ts",
    )
    logger.info("索引已确保")


def aggregate_minute(db, device_id: str, minute_start_ts: int) -> dict:
    """
    聚合指定分钟的原始数据，写入 metrics_1min。

    参数:
        db              - pymongo 数据库
        device_id       - 设备 ID
        minute_start_ts- 分钟起始时间戳（毫秒，对齐到整分钟）

    返回:
        dict - 聚合结果文档
    """
    # 1. 采集该分钟原始数据
    rows = collect_minute_raw(db, device_id, minute_start_ts)
    if not rows:
        return None

    # 2. 计算 ΔP/MP
    enrich_rows(rows)

    # 3. 过滤待机
    vent_rows, standby_rows = filter_ventilated(rows)
    use_rows = vent_rows if vent_rows else []

    if not use_rows:
        # 全部待机，仍写一条占位，但「继承」上一分钟的滚动累计，
        # 避免设备待机/过渡分钟把「通气全程」累计清零。
        prev = db[COLL_1MIN].find_one(
            {"deviceId": device_id}, sort=[("minute", -1)]
        )
        pc = {}
        if prev and prev.get("minute", 0) < minute_start_ts:
            pc = {
                "cum_dp_over_min": prev.get("cum_dp_over_min", 0.0) or 0.0,
                "cum_mp_over_min_17": prev.get("cum_mp_over_min_17", 0.0) or 0.0,
                "cum_mp_over_min_18": prev.get("cum_mp_over_min_18", 0.0) or 0.0,
                "cum_mp_over_min_20": prev.get("cum_mp_over_min_20", 0.0) or 0.0,
                "cum_dp_auc_above": prev.get("cum_dp_auc_above", 0.0) or 0.0,
                "cum_mp_auc_above_17": prev.get("cum_mp_auc_above_17", 0.0) or 0.0,
                "cum_mp_auc_above_18": prev.get("cum_mp_auc_above_18", 0.0) or 0.0,
                "cum_mp_auc_above_20": prev.get("cum_mp_auc_above_20", 0.0) or 0.0,
                "cum_energy": prev.get("cum_energy", 0.0) or 0.0,
                "compliance_mean": prev.get("compliance_mean"),
                "vent_duration_min": prev.get("vent_duration_min", 0.0) or 0.0,
                "cum_gap_min": prev.get("cum_gap_min", 0.0) or 0.0,
                "cum_n_gaps": prev.get("cum_n_gaps", 0) or 0,
                "dp_source": prev.get("dp_source", "none"),
                "mp_source": prev.get("mp_source", "none"),
                "cumulative_risk_level": prev.get("cumulative_risk_level", 1),
            }
        cum_risk = pc.get("cumulative_risk_level", 1)
        doc = {
            "deviceId": device_id,
            "minute": minute_start_ts,
            "minuteISO": datetime.fromtimestamp(
                minute_start_ts / 1000, tz=timezone.utc
            ).isoformat(),
            "vent_points": 0,
            "standby_points": len(rows),
            "is_ventilating": False,
            "dp_mean": None, "dp_max": None,
            "dp_over_count": 0, "dp_over_pct": 0.0, "dp_auc": 0.0,
            "mp_mean": None, "mp_max": None,
            "mp_over_count": 0, "mp_over_pct": 0.0, "mp_auc": 0.0,
            "cum_dp_over_min": pc.get("cum_dp_over_min", 0.0),
            "cum_mp_over_min_17": pc.get("cum_mp_over_min_17", 0.0),
            "cum_mp_over_min_18": pc.get("cum_mp_over_min_18", 0.0),
            "cum_mp_over_min_20": pc.get("cum_mp_over_min_20", 0.0),
            "cum_dp_auc_above": pc.get("cum_dp_auc_above", 0.0),
            "cum_mp_auc_above_17": pc.get("cum_mp_auc_above_17", 0.0),
            "cum_mp_auc_above_18": pc.get("cum_mp_auc_above_18", 0.0),
            "cum_mp_auc_above_20": pc.get("cum_mp_auc_above_20", 0.0),
            "cum_energy": pc.get("cum_energy", 0.0),
            "compliance_mean": pc.get("compliance_mean"),
            "vent_duration_min": pc.get("vent_duration_min", 0.0),
            "cum_gap_min": pc.get("cum_gap_min", 0.0),
            "cum_n_gaps": pc.get("cum_n_gaps", 0),
            "dp_source": pc.get("dp_source", "none"),
            "mp_source": pc.get("mp_source", "none"),
            "risk_level_instant": 1,
            "cumulative_risk_level": cum_risk,
            "risk_level": cum_risk,
            "risk_label": RISK_LABELS.get(cum_risk, "L1 正常"),
            "pip_mean": None, "peep_mean": None, "vt_mean": None,
            "vti_mean": None, "vte_mean": None,
            "rr_mean": None, "plat_mean": None, "crs_mean": None,
        }
    else:
        # 4. 计算暴露指标
        exposure = compute_exposure(use_rows)

        # 原始参数均值快照
        def _mean(key):
            vals = [r.get(key, float("nan")) for r in use_rows]
            vals = [v for v in vals if not math.isnan(v)]
            return sum(vals) / len(vals) if vals else None

        # 5. 读取上一分钟累积值
        prev = db[COLL_1MIN].find_one(
            {"deviceId": device_id},
            sort=[("minute", -1)],
        )
        # 确保 prev 是更早的分钟
        prev_cum_dp = 0.0
        prev_cum_mp_17 = 0.0
        prev_cum_mp_18 = 0.0
        prev_cum_mp_20 = 0.0
        prev_cum_dp_auc = 0.0
        prev_cum_mp_auc_17 = 0.0
        prev_cum_mp_auc_18 = 0.0
        prev_cum_mp_auc_20 = 0.0
        prev_cum_energy = 0.0
        prev_vent_min = 0.0
        prev_gap_min = 0.0
        prev_n_gaps = 0
        prev_crs_w = 0.0
        if prev and prev.get("minute", 0) < minute_start_ts:
            prev_cum_dp = prev.get("cum_dp_over_min", 0.0) or 0.0
            prev_cum_mp_17 = prev.get("cum_mp_over_min_17", 0.0) or 0.0
            prev_cum_mp_18 = prev.get("cum_mp_over_min_18", 0.0) or 0.0
            prev_cum_mp_20 = prev.get("cum_mp_over_min_20", 0.0) or 0.0
            prev_cum_dp_auc = prev.get("cum_dp_auc_above", 0.0) or 0.0
            prev_cum_mp_auc_17 = prev.get("cum_mp_auc_above_17", 0.0) or 0.0
            prev_cum_mp_auc_18 = prev.get("cum_mp_auc_above_18", 0.0) or 0.0
            prev_cum_mp_auc_20 = prev.get("cum_mp_auc_above_20", 0.0) or 0.0
            prev_cum_energy = prev.get("cum_energy", 0.0) or 0.0
            prev_vent_min = prev.get("vent_duration_min", 0.0) or 0.0
            prev_gap_min = prev.get("cum_gap_min", 0.0) or 0.0
            prev_n_gaps = int(prev.get("cum_n_gaps", 0) or 0)
            prev_crs_w = prev.get("cum_crs_weighted", 0.0) or 0.0

        # ── 本分钟增量 ──
        # 关键修正：暴露发生在「两个上报点之间」，单靠本分钟内部的点积分不出来
        # （实测多数分钟只有 1 个批次）。故用 boundary_increment 在
        # 「上一聚合点 → 本分钟」的跨分钟区间上积分，并按 ≤4h 前向填充 / >4h 断流处理。
        cur_dp = exposure["dp"]["mean"]
        cur_mp = exposure["mp"]["mean"]
        cur_dp = float("nan") if cur_dp is None else cur_dp
        cur_mp = float("nan") if cur_mp is None else cur_mp

        inc = boundary_increment(
            prev.get("minute") if prev else None,
            _as_float(prev.get("dp_mean")) if prev else float("nan"),
            _as_float(prev.get("mp_mean")) if prev else float("nan"),
            minute_start_ts, cur_dp, cur_mp,
            MAX_FORWARD_FILL_MIN,
        ) if (prev and prev.get("minute", 0) < minute_start_ts) else None

        if inc is None:
            # 没有更早的锚点（首分钟）：退化为本分钟内部积分
            inner = exposure["cum_over_minutes"]
            inner_auc = exposure["cum_auc_above"]
            inc = {
                "dp_over_min": inner["dp"] or 0,
                "mp17": inner["mp17"] or 0, "mp18": inner["mp18"] or 0,
                "mp20": inner["mp20"] or 0,
                "dp_auc": inner_auc["dp"] or 0,
                "mp17_auc": inner_auc["mp17"] or 0,
                "mp18_auc": inner_auc["mp18"] or 0,
                "mp20_auc": inner_auc["mp20"] or 0,
                "vent_min": float(exposure.get("vent_minutes") or 0.0),
                "gap_min": float(exposure.get("gap_minutes") or 0.0),
                "n_gaps": exposure.get("n_gaps", 0),
                "energy_j": exposure.get("cum_energy_j") or 0.0,
            }

        inc_dp_over_min = inc["dp_over_min"]
        inc_mp_over_min_17 = inc["mp17"]
        inc_mp_over_min_18 = inc["mp18"]
        inc_mp_over_min_20 = inc["mp20"]
        inc_dp_auc_above = inc["dp_auc"]
        inc_mp_auc_above_17 = inc["mp17_auc"]
        inc_mp_auc_above_18 = inc["mp18_auc"]
        inc_mp_auc_above_20 = inc["mp20_auc"]
        inc_energy = inc["energy_j"]
        inc_vent_min = inc["vent_min"]
        inc_gap_min = inc["gap_min"]

        # 滚动累积量（通气全程，不随查询窗口截断）
        cum_dp_over_min = prev_cum_dp + inc_dp_over_min
        cum_mp_over_min_17 = prev_cum_mp_17 + inc_mp_over_min_17
        cum_mp_over_min_18 = prev_cum_mp_18 + inc_mp_over_min_18
        cum_mp_over_min_20 = prev_cum_mp_20 + inc_mp_over_min_20
        cum_dp_auc_above = prev_cum_dp_auc + inc_dp_auc_above
        cum_mp_auc_above_17 = prev_cum_mp_auc_17 + inc_mp_auc_above_17
        cum_mp_auc_above_18 = prev_cum_mp_auc_18 + inc_mp_auc_above_18
        cum_mp_auc_above_20 = prev_cum_mp_auc_20 + inc_mp_auc_above_20
        cum_energy = prev_cum_energy + inc_energy
        vent_min_total = prev_vent_min + inc_vent_min
        gap_min_total = prev_gap_min + inc_gap_min
        n_gaps_total = prev_n_gaps + inc["n_gaps"]

        # 顺应性分层（用本分钟均值判定当前分层）
        compliance_mean = exposure.get("compliance_mean")
        compliance_mean = compliance_mean if (compliance_mean is not None
                                             and not math.isnan(compliance_mean)) else 0.0

        # 顺应性：按「有效通气时长」加权滚动（与 weighted_mean_series 同一口径），
        # 避免取最后一分钟的瞬时值导致全程 / 窗口两套结论不一致。
        prev_crs_inst = _as_float(prev.get("compliance_instant")) if prev else float("nan")
        crs_pair_vals = [v for v in (prev_crs_inst, compliance_mean)
                         if not math.isnan(v)]
        crs_pair = (sum(crs_pair_vals) / len(crs_pair_vals)) if crs_pair_vals else float("nan")
        crs_inc = (crs_pair * inc_vent_min) if (not math.isnan(crs_pair)
                                                and inc_vent_min) else 0.0
        cum_crs_w = prev_crs_w + crs_inc
        compliance_weighted = (cum_crs_w / vent_min_total) if vent_min_total > 0 else float("nan")
        # 分层用「加权滚动均值」，与 24h 窗口口径保持一致
        stratum = "high" if (not math.isnan(compliance_weighted)
                             and compliance_weighted > COMPLIANCE_STRATUM_THRESHOLD) else "low"

        # 双维度风险：瞬时（单值） + 累积（暴露，按高暴露小时数 + 顺应性分层）
        instant_risk = exposure["risk_level"]
        dp_over_hours = cum_dp_over_min / 60.0
        mp_over_hours = (cum_mp_over_min_18 / 60.0) if stratum == "high" else (cum_mp_over_min_20 / 60.0)
        cumulative_risk = classify_cumulative_risk(dp_over_hours, mp_over_hours, stratum)
        merged_risk = max(instant_risk, cumulative_risk)

        doc = {
            "deviceId": device_id,
            "minute": minute_start_ts,
            "minuteISO": datetime.fromtimestamp(
                minute_start_ts / 1000, tz=timezone.utc
            ).isoformat(),
            "is_ventilating": True,
            "vent_points": len(use_rows),
            "standby_points": len(standby_rows),
            "dp_mean": _nan_to_none(exposure["dp"]["mean"]),
            "dp_max": _nan_to_none(exposure["dp"]["max"]),
            "dp_over_count": exposure["dp"]["over_count"],
            "dp_over_pct": exposure["dp"]["over_pct"],
            "dp_auc": inc_dp_auc_above,
            "mp_mean": _nan_to_none(exposure["mp"]["mean"]),
            "mp_max": _nan_to_none(exposure["mp"]["max"]),
            "mp_over_count": exposure["mp"]["over_count"],
            "mp_over_pct": exposure["mp"]["over_pct"],
            "mp_auc": inc_mp_auc_above_17,
            # 累积暴露（双轨制：高暴露分钟数 / 超阈 AUC / 总机械能 / 顺应性）
            "cum_dp_over_min": cum_dp_over_min,
            "cum_mp_over_min_17": cum_mp_over_min_17,
            "cum_mp_over_min_18": cum_mp_over_min_18,
            "cum_mp_over_min_20": cum_mp_over_min_20,
            "cum_dp_auc_above": cum_dp_auc_above,
            "cum_mp_auc_above_17": cum_mp_auc_above_17,
            "cum_mp_auc_above_18": cum_mp_auc_above_18,
            "cum_mp_auc_above_20": cum_mp_auc_above_20,
            "cum_energy": cum_energy,
            # 有效通气时长 / 断流（URS FR-01、FR-04）
            "vent_duration_min": vent_min_total,
            "cum_gap_min": gap_min_total,
            "cum_n_gaps": n_gaps_total,
            # 计算模式溯源（static / dynamic）—— 动态降级时界面打 *Dyn*
            "dp_source": exposure.get("dp_source", "none"),
            "mp_source": exposure.get("mp_source", "none"),
            # 全程口径用「按通气时长加权」的滚动均值，与 24h 窗口口径保持一致
            "compliance_mean": _nan_to_none(compliance_weighted),
            "compliance_instant": _nan_to_none(compliance_mean),
            "cum_crs_weighted": cum_crs_w,
            "compliance_stratum": stratum,
            # 风险（双维度合并）
            "risk_level_instant": instant_risk,
            "cumulative_risk_level": cumulative_risk,
            "risk_level": merged_risk,
            "risk_label": RISK_LABELS.get(merged_risk, "L1 正常"),
            # 原始参数快照
            "pip_mean": _nan_to_none(_mean("PIP")),
            "peep_mean": _nan_to_none(_mean("PEEP")),
            # Vt 口径：Vti(110) 优先，回退 Vte(106)（2026-09-09 用户确认）
            "vt_mean": _nan_to_none(_mean("VT")),
            "vti_mean": _nan_to_none(_mean("Vti")),
            "vte_mean": _nan_to_none(_mean("Vte")),
            "rr_mean": _nan_to_none(_mean("RR")),
            "plat_mean": _nan_to_none(_mean("Pplat")),
            "crs_mean": _nan_to_none(_mean("Cdyn")),
        }

    # 6. 写入（upsert）
    db[COLL_1MIN].update_one(
        {"deviceId": device_id, "minute": minute_start_ts},
        {"$set": doc},
        upsert=True,
    )

    # 7. 风险升级时写预警
    if doc.get("risk_level", 1) >= 2:
        _check_and_alert(db, device_id, minute_start_ts, doc)

    return doc


def _check_and_alert(db, device_id: str, ts: int, doc: dict):
    """检查并写入预警事件（按 (risk_level, category) 5分钟窗口去重）

    预警分两类：
      - category="threshold"  ：瞬时单值越限（ΔP/MP 超过单点阈值）
      - category="cumulative" ：累积暴露越限（高暴露小时数超阈值，按顺应性分层）
    两类独立去重、独立存储，互不覆盖。
    """
    risk = doc.get("risk_level", 1)
    if risk < 2:
        return

    instant = doc.get("risk_level_instant", 1)
    cum = doc.get("cumulative_risk_level", 1)

    # 判定主导类别：累积维度高于瞬时维度 → 累积类；否则单值类
    if cum >= 2 and cum >= instant:
        category = "cumulative"
    else:
        category = "threshold"

    # 5分钟窗口内同 (级别, 类别) 去重
    recent = db[COLL_ALERTS].find_one({
        "deviceId": device_id,
        "ts": {"$gte": ts - 5 * 60 * 1000},
        "risk_level": risk,
        "category": category,
    })
    if recent:
        return

    # 构造预警消息：单值部分
    parts = []
    if doc.get("dp_max") and doc["dp_max"] > DP_THRESHOLD:
        parts.append(f"ΔP={doc['dp_max']:.1f} 超阈值({DP_THRESHOLD:.0f})")
    if doc.get("mp_max") and doc["mp_max"] > MP_THRESHOLD:
        parts.append(f"MP={doc['mp_max']:.1f} 超阈值({MP_THRESHOLD:.0f})")
    if doc.get("dp_over_pct", 0) > 20:
        parts.append(f"ΔP超阈占比{doc['dp_over_pct']:.0f}%")
    if doc.get("mp_over_pct", 0) > 20:
        parts.append(f"MP超阈占比{doc['mp_over_pct']:.0f}%")

    # 累积部分（高暴露小时数 + 顺应性分层）
    cparts = []
    if cum >= 2:
        dp_over_hours = (doc.get("cum_dp_over_min", 0) or 0) / 60.0
        stratum = doc.get("compliance_stratum", "high")
        if stratum == "high":
            mp_over_hours = (doc.get("cum_mp_over_min_18", 0) or 0) / 60.0
            mp_thr = MP_HIGH_STRATUM_THRESHOLD
        else:
            mp_over_hours = (doc.get("cum_mp_over_min_20", 0) or 0) / 60.0
            mp_thr = MP_LOW_STRATUM_THRESHOLD
        if dp_over_hours >= CUM_DP_OVER_HOURS_L3:
            cparts.append(f"ΔP高暴露{dp_over_hours:.1f}h")
        if mp_over_hours >= (CUM_MP_OVER_HOURS_L3_HIGH if stratum == "high"
                             else CUM_MP_OVER_HOURS_L3_LOW):
            cparts.append(f"MP≥{mp_thr:.0f}高暴露{mp_over_hours:.1f}h({stratum}顺应性)")

    if category == "cumulative":
        # 累积类：以累积原因为主
        if cparts:
            message = "；".join(cparts)
        elif parts:
            message = "；".join(parts)
        else:
            message = "累积暴露升高"
    else:
        message = "；".join(parts) if parts else "风险升高"

    alert = {
        "deviceId": device_id,
        "ts": ts,
        "tsISO": datetime.fromtimestamp(ts / 1000, tz=timezone.utc).isoformat(),
        "risk_level": risk,
        "risk_label": RISK_LABELS.get(risk, ""),
        "category": category,
        "message": message,
        "detail": "；".join(parts + cparts) if (parts or cparts) else "风险升高",
        "dp_max": doc.get("dp_max"),
        "mp_max": doc.get("mp_max"),
        "dp_over_pct": doc.get("dp_over_pct", 0),
        "mp_over_pct": doc.get("mp_over_pct", 0),
        "cum_dp_over_min": doc.get("cum_dp_over_min"),
        "cum_mp_over_min_18": doc.get("cum_mp_over_min_18"),
        "cum_mp_over_min_20": doc.get("cum_mp_over_min_20"),
        "cum_energy_j": doc.get("cum_energy"),
        "compliance_stratum": doc.get("compliance_stratum"),
        # 生命周期状态：写入即「活动中、未确认」，由 POST /api/alerts/{id}/ack 置为已确认
        "active": True,
        "acknowledged": False,
    }
    db[COLL_ALERTS].insert_one(alert)
    logger.info(f"预警写入[{category}]: {alert['message']} @ {alert['tsISO']}")


def backfill(db, device_id: str = DEVICE_ID, hours: float = 24):
    """
    历史回填：对最近 N 小时逐分钟聚合。

    参数:
        db        - pymongo 数据库
        device_id - 设备 ID
        hours     - 回填小时数
    """
    from .collector import get_time_range, get_data_minutes
    _ensure_indexes(db)

    _, latest_ts = get_time_range(db, device_id)
    if latest_ts == 0:
        logger.error("无数据")
        return

    # 对齐到整分钟
    start_ts = latest_ts - int(hours * 3600 * 1000)
    start_ts = (start_ts // 60000) * 60000  # floor to minute

    # 只处理「有上报数据」的分钟（性能：避免 720h=43200 次空查询）
    minutes = get_data_minutes(db, device_id, start_ts, latest_ts)
    logger.info(f"窗口内有数据的分钟: {len(minutes)}")

    total = 0
    for ts in minutes:
        doc = aggregate_minute(db, device_id, ts)
        if doc:
            total += 1

    logger.info(f"回填完成: {total} 分钟已聚合")
    return total


def run_continuous(db, device_id: str = DEVICE_ID, poll_interval: float = 10.0):
    """
    守护进程模式：持续监听新数据，每分钟聚合。

    参数:
        db              - pymongo 数据库
        device_id       - 设备 ID
        poll_interval   - 轮询间隔（秒）
    """
    from .collector import get_time_range
    _ensure_indexes(db)

    logger.info(f"聚合守护进程启动, 设备={device_id}, 轮询={poll_interval}s")

    # 找到已聚合的最新分钟
    last_agg = db[COLL_1MIN].find_one(
        {"deviceId": device_id}, sort=[("minute", -1)]
    )
    if last_agg:
        next_minute = last_agg["minute"] + 60000
    else:
        _, latest_ts = get_time_range(db, device_id)
        next_minute = (latest_ts // 60000) * 60000

    logger.info(f"起始聚合分钟: {datetime.fromtimestamp(next_minute/1000, tz=timezone.utc).isoformat()}")

    while True:
        _, latest_ts = get_time_range(db, device_id)
        now_minute = (latest_ts // 60000) * 60000

        # 聚合所有未处理的分钟
        while next_minute < now_minute:
            # 确保该分钟已完整（当前时间超过该分钟结束）
            doc = aggregate_minute(db, device_id, next_minute)
            if doc:
                logger.debug(
                    f"已聚合 {doc.get('minuteISO')}: "
                    f"vent={doc.get('vent_points', 0)}, "
                    f"risk={doc.get('risk_label', '')}"
                )
            next_minute += 60000

        time.sleep(poll_interval)
