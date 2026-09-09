# -*- coding: utf-8 -*-
"""
urs_probe2.py - 全量数据画像（只读）：paramId 覆盖 / 上报节奏 / 断流分布 / 新鲜度
用 MongoDB 聚合在服务端统计，不把全量文档拉到客户端。
"""

import sys
import os
import math
from datetime import datetime, timezone
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pymongo import MongoClient
from lung_protection_cockpit.config import (
    MONGO_URI, MONGO_DB, COLL_RAW, COLL_1MIN, DEVICE_ID, PARAM_MAP,
)


def to_float(v):
    try:
        return float(v)
    except (ValueError, TypeError):
        return float("nan")


def main():
    cli = MongoClient(MONGO_URI, serverSelectionTimeoutMS=8000)
    db = cli[MONGO_DB]
    coll = db[COLL_RAW]

    print("=" * 78)
    print(f"设备 {DEVICE_ID} 全量画像")
    total = coll.count_documents({"deviceId": DEVICE_ID})
    print(f"  该设备文档总数: {total:,}")

    newest = coll.find_one({"deviceId": DEVICE_ID}, sort=[("timeStamp", -1)])
    oldest = coll.find_one({"deviceId": DEVICE_ID}, sort=[("timeStamp", 1)])
    end_ts = int(newest["timeStamp"])
    start_ts = int(oldest["timeStamp"])
    now_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
    print(f"  最早: {datetime.fromtimestamp(start_ts/1000, tz=timezone.utc).isoformat()}")
    print(f"  最晚: {datetime.fromtimestamp(end_ts/1000, tz=timezone.utc).isoformat()}")
    print(f"  现在: {datetime.fromtimestamp(now_ms/1000, tz=timezone.utc).isoformat()}")
    print(f"  ★ 数据滞后: {(now_ms-end_ts)/3600000:.2f} h（>1h 说明设备已离线/停报）")
    print(f"  跨度: {(end_ts-start_ts)/3600000:.2f} h")

    # 所有 deviceId
    print("\n" + "-" * 78)
    print("【0】库内 deviceId 分布（取前 10）")
    for d in coll.aggregate([{"$group": {"_id": "$deviceId", "n": {"$sum": 1}}},
                             {"$sort": {"n": -1}}, {"$limit": 10}]):
        last = coll.find_one({"deviceId": d["_id"]}, sort=[("timeStamp", -1)])
        lag = (now_ms - int(last["timeStamp"])) / 3600000.0
        print(f"  {str(d['_id']):<20} {d['n']:>10,} 条   最新 {lag:>9.2f} h 前")

    # paramId 覆盖（全量）
    print("\n" + "-" * 78)
    print("【1】paramId 全量覆盖（服务端聚合）")
    print(f"{'pid':>5} {'名称':<12} {'条数':>9} {'占比':>7}  最早/最晚出现")
    pid_stats = {}
    for d in coll.aggregate([
        {"$match": {"deviceId": DEVICE_ID}},
        {"$group": {"_id": "$paramId", "n": {"$sum": 1},
                    "tmin": {"$min": "$timeStamp"}, "tmax": {"$max": "$timeStamp"}}},
        {"$sort": {"n": -1}},
    ]):
        pid = d["_id"]
        pid_stats[pid] = d
        name = PARAM_MAP.get(pid, "?")
        ta = datetime.fromtimestamp(int(d["tmin"]) / 1000, tz=timezone.utc).strftime("%m-%d %H:%M")
        tb = datetime.fromtimestamp(int(d["tmax"]) / 1000, tz=timezone.utc).strftime("%m-%d %H:%M")
        print(f"{pid:>5} {str(name):<12} {d['n']:>9,} {d['n']/total*100:>6.1f}%  {ta} ~ {tb}")

    # 上报节奏（全量时间戳）
    print("\n" + "-" * 78)
    print("【2】上报节奏与断流（全量）")
    tss = sorted(set(int(d["timeStamp"]) for d in coll.find(
        {"deviceId": DEVICE_ID}, {"_id": 0, "timeStamp": 1}).sort("timeStamp", 1)))
    print(f"  不同时间戳(批次)数: {len(tss)}")
    gaps = [(tss[i] - tss[i-1]) / 1000.0 for i in range(1, len(tss))]
    gs = sorted(gaps)
    def p(pv):
        return gs[min(len(gs)-1, int(len(gs)*pv))]
    print(f"  相邻批次间隔(s): min={min(gaps):.0f} p10={p(.1):.0f} p25={p(.25):.0f} p50={p(.5):.0f} "
          f"p75={p(.75):.0f} p90={p(.9):.0f} p99={p(.99):.0f} max={max(gaps):.0f}")
    print(f"  平均间隔 {sum(gaps)/len(gaps):.0f}s = {sum(gaps)/len(gaps)/60:.1f} min")
    for lim, lab in [(60, ">1min"), (240, ">4min(断流候选)"), (3600, ">1h"), (14400, ">4h")]:
        print(f"  间隔 {lab:16s}: {len([g for g in gaps if g > lim]):>6} 段")
    span_h = (tss[-1] - tss[0]) / 3600000.0
    print(f"  批次密度: {len(tss)/span_h:.2f} 批/小时")

    # 有数据的分钟数 vs 理论分钟数
    per_min = defaultdict(int)
    for t in tss:
        per_min[t // 60000] += 1
    minutes_total = int((tss[-1] - tss[0]) / 60000) + 1
    print(f"  有数据整分钟: {len(per_min)} / 理论 {minutes_total} → DCR(全跨度) = "
          f"{len(per_min)/minutes_total*100:.2f}%")

    w_start = end_ts - 24 * 3600 * 1000
    in24 = [t for t in tss if t >= w_start]
    pm24 = defaultdict(int)
    for t in in24:
        pm24[t // 60000] += 1
    print(f"  最近 24h（截至最新数据）: {len(in24)} 批 / {len(pm24)} 个有数据分钟 → DCR = "
          f"{len(pm24)/1440*100:.2f}%")

    # 关键参数批次内可用率
    print("\n" + "-" * 78)
    print("【3】URS 必需参数在每个批次中的可用性（全量）")
    need = {101: "PIP(峰压)", 102: "Pplat(平台压)", 104: "PEEP", 106: "Vte(潮气量)",
            113: "ftotal(总频率)", 160: "DrivePress(设备ΔP)"}
    for pid, label in need.items():
        n = pid_stats.get(pid, {}).get("n", 0)
        extra = "占总文档 %.2f%%" % (n / total * 100) if n else "★ 完全缺失"
        print(f"  {pid:>4} {label:<22} {n:>8,} 条  {extra}")

    # 数值分布
    print("\n" + "-" * 78)
    print("【4】关键参数数值分布（抽样最近 300 条每参数）")
    for pid, label in need.items():
        docs = list(coll.find({"deviceId": DEVICE_ID, "paramId": pid},
                              {"_id": 0, "value": 1})
                    .sort("timeStamp", -1).limit(300))
        if not docs:
            continue
        vals = [to_float(d.get("value")) for d in docs]
        nums = [v for v in vals if not math.isnan(v)]
        bad = [d.get("value") for d in docs if math.isnan(to_float(d.get("value")))]
        if nums:
            print(f"  {pid:>4} {label:<22} n={len(docs):>4} 有效={len(nums):>4} "
                  f"min={min(nums):.1f} max={max(nums):.1f} mean={sum(nums)/len(nums):.2f}"
                  + ("  非数值样例=%s" % bad[:3] if bad else ""))
        else:
            print(f"  {pid:>4} {label:<22} n={len(docs)} 非数值样例={bad[:5]}")

    # 交叉一致性
    print("\n" + "-" * 78)
    print("【5】交叉一致性抽查（最近 3 个批次全参数）")
    for t in sorted(set(tss), reverse=True)[:3]:
        ds = list(coll.find({"deviceId": DEVICE_ID, "timeStamp": t},
                            {"_id": 0, "paramId": 1, "value": 1}))
        m = {d["paramId"]: d.get("value") for d in ds}
        print(f"\n  {datetime.fromtimestamp(t/1000, tz=timezone.utc).isoformat()}  参数数={len(ds)}")
        for pid in sorted(m.keys()):
            print(f"      {pid:>4} {str(PARAM_MAP.get(pid,'')):<12} = {m[pid]}")
        try:
            vt = float(m.get(106)); rr = float(m.get(113)); mve = float(m.get(111))
            print(f"      -> Vte*RR/1000 = {vt*rr/1000:.2f} L/min  vs  MVe = {mve} L/min")
        except (TypeError, ValueError):
            pass

    print("\n完成。")


if __name__ == "__main__":
    main()
