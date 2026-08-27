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
    COMPLIANCE_STRATUM_THRESHOLD,
    CUM_DP_OVER_HOURS_L3, CUM_DP_OVER_HOURS_L4,
    MP_HIGH_STRATUM_THRESHOLD, MP_LOW_STRATUM_THRESHOLD,
    CUM_MP_OVER_HOURS_L3_HIGH, CUM_MP_OVER_HOURS_L4_HIGH,
    CUM_MP_OVER_HOURS_L3_LOW, CUM_MP_OVER_HOURS_L4_LOW,
)
from .collector import get_db, collect_minute_raw
from .calculator import (
    enrich_rows, filter_ventilated, compute_exposure, classify_cumulative_risk,
)

logger = logging.getLogger("lung_cockpit.aggregator")


def _nan_to_none(v):
    """NaN 转 None（MongoDB 不支持 NaN 存储）"""
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    return v


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
        # 全部待机，仍写一条占位
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
            "cum_dp_over_min": 0.0,
            "cum_mp_over_min_18": 0.0,
            "cum_mp_over_min_20": 0.0,
            "cum_dp_auc_above": 0.0,
            "cum_mp_auc_above_17": 0.0,
            "cum_mp_auc_above_18": 0.0,
            "cum_mp_auc_above_20": 0.0,
            "cum_energy": 0.0,
            "compliance_mean": None,
            "vent_duration_min": 0.0,
            "risk_level_instant": 1,
            "cumulative_risk_level": 1,
            "risk_level": 1,
            "risk_label": RISK_LABELS[1],
            "pip_mean": None, "peep_mean": None, "vt_mean": None,
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
        prev_cum_mp_18 = 0.0
        prev_cum_mp_20 = 0.0
        prev_cum_dp_auc = 0.0
        prev_cum_mp_auc_17 = 0.0
        prev_cum_mp_auc_18 = 0.0
        prev_cum_mp_auc_20 = 0.0
        prev_cum_energy = 0.0
        prev_vent_min = 0.0
        if prev and prev.get("minute", 0) < minute_start_ts:
            prev_cum_dp = prev.get("cum_dp_over_min", 0.0) or 0.0
            prev_cum_mp_18 = prev.get("cum_mp_over_min_18", 0.0) or 0.0
            prev_cum_mp_20 = prev.get("cum_mp_over_min_20", 0.0) or 0.0
            prev_cum_dp_auc = prev.get("cum_dp_auc_above", 0.0) or 0.0
            prev_cum_mp_auc_17 = prev.get("cum_mp_auc_above_17", 0.0) or 0.0
            prev_cum_mp_auc_18 = prev.get("cum_mp_auc_above_18", 0.0) or 0.0
            prev_cum_mp_auc_20 = prev.get("cum_mp_auc_above_20", 0.0) or 0.0
            prev_cum_energy = prev.get("cum_energy", 0.0) or 0.0
            prev_vent_min = prev.get("vent_duration_min", 0.0) or 0.0

        # 本分钟增量（来自本分钟窗口的暴露计算）
        dt_min = len(use_rows) * 4 / 60  # 约 15 点 × 4s = 1min
        inc_dp_over_min = exposure["cum_over_minutes"]["dp"] or 0
        inc_mp_over_min_17 = exposure["cum_over_minutes"]["mp17"] or 0
        inc_mp_over_min_18 = exposure["cum_over_minutes"]["mp18"] or 0
        inc_mp_over_min_20 = exposure["cum_over_minutes"]["mp20"] or 0
        inc_dp_auc_above = exposure["cum_auc_above"]["dp"] or 0
        inc_mp_auc_above_17 = exposure["cum_auc_above"]["mp17"] or 0
        inc_mp_auc_above_18 = exposure["cum_auc_above"]["mp18"] or 0
        inc_mp_auc_above_20 = exposure["cum_auc_above"]["mp20"] or 0
        inc_energy = exposure["cum_energy_j"] or 0

        # 滚动累积量（通气全程，不随查询窗口截断）
        cum_dp_over_min = prev_cum_dp + inc_dp_over_min
        cum_mp_over_min_18 = prev_cum_mp_18 + inc_mp_over_min_18
        cum_mp_over_min_20 = prev_cum_mp_20 + inc_mp_over_min_20
        cum_dp_auc_above = prev_cum_dp_auc + inc_dp_auc_above
        cum_mp_auc_above_17 = prev_cum_mp_auc_17 + inc_mp_auc_above_17
        cum_mp_auc_above_18 = prev_cum_mp_auc_18 + inc_mp_auc_above_18
        cum_mp_auc_above_20 = prev_cum_mp_auc_20 + inc_mp_auc_above_20
        cum_energy = prev_cum_energy + inc_energy
        vent_min_total = prev_vent_min + dt_min

        # 顺应性分层（用本分钟均值判定当前分层）
        compliance_mean = exposure.get("compliance_mean")
        compliance_mean = compliance_mean if (compliance_mean is not None
                                             and not math.isnan(compliance_mean)) else 0.0
        stratum = "high" if compliance_mean > COMPLIANCE_STRATUM_THRESHOLD else "low"

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
            "cum_mp_over_min_18": cum_mp_over_min_18,
            "cum_mp_over_min_20": cum_mp_over_min_20,
            "cum_dp_auc_above": cum_dp_auc_above,
            "cum_mp_auc_above_17": cum_mp_auc_above_17,
            "cum_mp_auc_above_18": cum_mp_auc_above_18,
            "cum_mp_auc_above_20": cum_mp_auc_above_20,
            "cum_energy": cum_energy,
            "compliance_mean": _nan_to_none(exposure.get("compliance_mean")),
            "compliance_stratum": stratum,
            "vent_duration_min": vent_min_total,
            # 风险（双维度合并）
            "risk_level_instant": instant_risk,
            "cumulative_risk_level": cumulative_risk,
            "risk_level": merged_risk,
            "risk_label": RISK_LABELS.get(merged_risk, "L1 正常"),
            # 原始参数快照
            "pip_mean": _nan_to_none(_mean("PIP")),
            "peep_mean": _nan_to_none(_mean("PEEP")),
            "vt_mean": _nan_to_none(_mean("Vte")),
            "rr_mean": _nan_to_none(_mean("ftotal")),
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
    from .collector import get_time_range
    _ensure_indexes(db)

    _, latest_ts = get_time_range(db, device_id)
    if latest_ts == 0:
        logger.error("无数据")
        return

    # 对齐到整分钟
    start_ts = latest_ts - int(hours * 3600 * 1000)
    start_ts = (start_ts // 60000) * 60000  # floor to minute

    total = 0
    ts = start_ts
    while ts <= latest_ts:
        doc = aggregate_minute(db, device_id, ts)
        if doc:
            total += 1
        ts += 60000  # +1 min

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
