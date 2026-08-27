# -*- coding: utf-8 -*-
"""
collector.py - 数据采集模块
从 MongoDB measure_param 集合按时间窗口采集原始参数，透视对齐为行。
"""

import logging
import math
from datetime import datetime, timezone
from typing import Optional

from pymongo import MongoClient

from .config import (
    MONGO_URI, MONGO_DB, COLL_RAW, COLL_1MIN, COLL_ALERTS, COLL_WORK_MODE, DEVICE_ID,
    PARAM_MAP, ALL_PARAM_IDS,
)

logger = logging.getLogger("lung_cockpit.collector")


def get_db():
    """获取 MongoDB 数据库句柄（惰性连接）"""
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    return client[MONGO_DB]


def to_float(v) -> float:
    """安全转换 value 字段（字符串，可能含 'OFF'/'---'）"""
    try:
        return float(v)
    except (ValueError, TypeError):
        return float("nan")


def ensure_indexes(db):
    """创建查询所需索引（幂等，可重复调用）。

    性能关键：measure_param 原始集合可达数百万文档，get_time_range 对
    timeStamp 排序取极值若无索引会全表扫描（本环境 ~7.4M 文档 → 每次数秒）。
    建索引后该扫描变为索引定位，<10ms。
    逐条创建并在各自 try 中处理，避免单条失败（如“索引已存在”）阻断其余。
    """
    specs = [
        (COLL_RAW, [("deviceId", 1), ("timeStamp", -1)], "raw_dev_ts", {}),
        (COLL_1MIN, [("deviceId", 1), ("minute", -1)], "dev_minute", {"unique": True}),
        (COLL_ALERTS, [("deviceId", 1), ("ts", -1)], "dev_ts_alert", {}),
    ]
    for coll, keys, name, opts in specs:
        kwargs = {"background": True}
        kwargs.update(opts)
        try:
            db[coll].create_index(keys, name=name, **kwargs)
            logger.info("索引已创建: %s.%s", coll, name)
        except Exception as e:  # 索引创建失败不应阻断主流程
            # 85 = IndexOptionsConflict：同名/同键索引已存在，属正常（幂等），不告警
            if getattr(e, "code", None) == 85 or "already exists" in str(e).lower():
                logger.info("索引已存在（跳过）: %s.%s", coll, name)
            else:
                logger.warning("ensure_indexes 部分失败 %s.%s: %s", coll, name, e)


def get_time_range(db, device_id: str = DEVICE_ID) -> tuple:
    """返回 (最早 ts, 最晚 ts) 毫秒时间戳"""
    coll = db[COLL_RAW]
    oldest = coll.find({"deviceId": device_id}).sort("timeStamp", 1).limit(1)
    newest = coll.find({"deviceId": device_id}).sort("timeStamp", -1).limit(1)
    old = next(oldest, None)
    new = next(newest, None)
    if not old or not new:
        return (0, 0)
    return (int(old["timeStamp"]), int(new["timeStamp"]))


def collect_raw(
    db,
    device_id: str = DEVICE_ID,
    start_ts: Optional[int] = None,
    end_ts: Optional[int] = None,
    hours: float = 2.0,
) -> list:
    """
    采集原始参数并透视对齐为行。

    参数:
        db          - pymongo 数据库句柄
        device_id   - 设备 ID
        start_ts    - 起始时间戳（毫秒），None 则自动取最近 hours 小时
        end_ts      - 结束时间戳（毫秒），None 则取最新
        hours       - 当 start_ts=None 时使用的回溯小时数

    返回:
        list[dict]  - 按时间戳排序，每个 dict 含：
            ts (int), dt (datetime), 以及各参数名: float
            额外含 "dP" 和 "MP" 已计算字段（由 calculator 填充，此处仅原始参数）
    """
    coll = db[COLL_RAW]

    if end_ts is None:
        latest = coll.find({"deviceId": device_id}).sort("timeStamp", -1).limit(1)
        latest_doc = next(latest, None)
        if not latest_doc:
            return []
        end_ts = int(latest_doc["timeStamp"])

    if start_ts is None:
        start_ts = end_ts - int(hours * 3600 * 1000)

    query = {
        "deviceId": device_id,
        "timeStamp": {"$gte": start_ts, "$lte": end_ts},
        "paramId": {"$in": ALL_PARAM_IDS},
    }
    cursor = coll.find(
        query,
        {"_id": 0, "paramId": 1, "value": 1, "timeStamp": 1, "unitName": 1, "name": 1},
    )

    # 按 timeStamp 分组（pivot）
    rows = {}       # ts -> {param_name: value}
    units = {}      # param_name -> unit
    total_raw = 0

    for doc in cursor:
        total_raw += 1
        ts = int(doc["timeStamp"])
        pid = doc["paramId"]
        pname = PARAM_MAP.get(pid)
        if not pname:
            continue
        v = to_float(doc.get("value"))
        rows.setdefault(ts, {})[pname] = v
        if doc.get("unitName"):
            units[pname] = doc["unitName"]

    # 排序
    sorted_ts = sorted(rows.keys())
    result = []
    for ts in sorted_ts:
        r = rows[ts]
        r["ts"] = ts
        r["dt"] = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
        result.append(r)

    return result


def collect_minute_raw(db, device_id: str, minute_start_ts: int) -> list:
    """
    采集指定分钟（minute_start_ts 到 +60000ms）的原始参数。
    供 aggregator 使用。
    """
    return collect_raw(
        db, device_id,
        start_ts=minute_start_ts,
        end_ts=minute_start_ts + 60000 - 1,
    )


def get_current_work_mode(db, device_id: str = DEVICE_ID, at_ts: int = None) -> str:
    """
    获取指定时间点最近的通气模式。

    work_mode 集合仅在模式变化时写入（非定时），
    需按时间戳回溯查找 <= at_ts 的最近一条记录。
    """
    coll = db[COLL_WORK_MODE]
    query = {"deviceId": device_id}
    if at_ts is not None:
        query["timeStamp"] = {"$lte": at_ts}
    doc = coll.find_one(query, sort=[("timeStamp", -1)])
    if doc and doc.get("workMode"):
        return doc["workMode"]
    return "未知"
