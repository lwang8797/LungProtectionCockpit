# -*- coding: utf-8 -*-
"""
collector.py - 数据采集模块
从 MongoDB measure_param 集合按时间窗口采集原始参数，透视对齐为行。
"""

import math
from datetime import datetime, timezone
from typing import Optional

from pymongo import MongoClient

from .config import (
    MONGO_URI, MONGO_DB, COLL_RAW, COLL_WORK_MODE, DEVICE_ID,
    PARAM_MAP, ALL_PARAM_IDS,
)


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
