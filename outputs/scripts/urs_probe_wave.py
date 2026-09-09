# -*- coding: utf-8 -*-
"""
urs_probe_wave.py - 波形数据可行性探针（只读，时间窗限定，走 [deviceId,timeStamp] 索引）

目的：核实 wave_data 是否可用于
  (a) URS 首选的 MP 波形积分 ∫Paw·dV
  (b) 补全 1 分钟分辨率（当前 measure_param 平均 37 min 一批，过于稀疏）
"""

import sys
import os
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pymongo import MongoClient
from lung_protection_cockpit.config import MONGO_URI, MONGO_DB, DEVICE_ID


def ts(ms):
    return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).strftime("%m-%d %H:%M:%S.%f")[:-3]


def main():
    cli = MongoClient(MONGO_URI, serverSelectionTimeoutMS=15000)
    db = cli[MONGO_DB]
    w = db["wave_data"]

    first = w.find_one({"deviceId": DEVICE_ID}, sort=[("timeStamp", 1)])
    last = w.find_one({"deviceId": DEVICE_ID}, sort=[("timeStamp", -1)])
    t0, t1 = int(first["timeStamp"]), int(last["timeStamp"])
    print("=" * 78)
    print(f"【wave_data】设备 {DEVICE_ID}")
    print(f"  全量范围: {ts(t0)} ~ {ts(t1)}   跨度 {(t1-t0)/3600000:.2f} h")

    # 取中段 2 分钟做细粒度分析
    mid = (t0 + t1) // 2
    win_ms = 120000
    a, b = mid, mid + win_ms
    print(f"\n  分析窗口: {ts(a)} ~ {ts(b)}（2 分钟）")

    docs = list(w.find({"deviceId": DEVICE_ID, "timeStamp": {"$gte": a, "$lte": b}},
                       {"_id": 0}))
    print(f"  窗口内文档数: {len(docs):,}  → 平均 {len(docs)/120:.0f} doc/s")

    by_wave = {}
    for d in docs:
        by_wave.setdefault(d.get("waveId"), []).append(d)
    print(f"\n  通道 (waveId) 分布 —— 共 {len(by_wave)} 个通道:")
    for wid in sorted(by_wave.keys()):
        lst = by_wave[wid]
        tss = sorted(set(int(x["timeStamp"]) for x in lst))
        vals = []
        for x in lst[:5]:
            vals.append([round(float(v), 2) for v in (x.get("waveValues") or [])[:5]])
        # 采样率估计
        gaps = [(tss[i] - tss[i-1]) for i in range(1, len(tss))]
        gaps = [g for g in gaps if g > 0]
        rate = ""
        if gaps:
            gaps.sort()
            med = gaps[len(gaps) // 2]
            rate = f" 中位间隔 {med} ms → {1000.0/med:.1f} Hz"
        # 值范围
        allv = []
        for x in lst:
            for v in (x.get("waveValues") or []):
                try:
                    allv.append(float(v))
                except (TypeError, ValueError):
                    pass
        rng = ""
        if allv:
            rng = f" 值域 [{min(allv):.2f}, {max(allv):.2f}] 均值 {sum(allv)/len(allv):.2f}"
        print(f"    waveId={str(wid):<6} {len(lst):>7} 条 / {len(tss):>6} 个时间戳{rate}{rng}")
        print(f"           样例 waveValues={vals[0] if vals else []}  waveInsps={lst[0].get('waveInsps')}")

    # cmdId
    cmds = {}
    for d in docs:
        cmds[d.get("cmdId")] = cmds.get(d.get("cmdId"), 0) + 1
    print(f"\n  cmdId 分布: {cmds}")

    # 单时间戳包含哪些通道
    if docs:
        one_ts = int(docs[0]["timeStamp"])
        same = list(w.find({"deviceId": DEVICE_ID, "timeStamp": one_ts}, {"_id": 0}))
        print(f"\n  同一时间戳 {ts(one_ts)} 下的文档:")
        for x in same:
            print(f"    waveId={x.get('waveId')} cmdId={x.get('cmdId')} "
                  f"values={[round(float(v),2) for v in (x.get('waveValues') or [])[:6]]} "
                  f"insps={x.get('waveInsps')}")

    # ---------- pva_data ----------
    print("\n" + "=" * 78)
    print("【pva_data】")
    p = db["pva_data"]
    nd = p.count_documents({"deviceId": DEVICE_ID})
    print(f"  本设备文档数: {nd}")
    d = p.find_one({"deviceId": DEVICE_ID}) if nd else p.find_one({})
    if d:
        for k, v in d.items():
            s = repr(v)
            print(f"    {k} = {s[:200]}{' ...' if len(s) > 200 else ''}")

    # ---------- patientinfo ----------
    print("\n" + "=" * 78)
    print("【patientinfo】（脱敏相关）")
    for d in db["patientinfo"].find({}).limit(3):
        print("  " + " | ".join(f"{k}={str(v)[:40]}" for k, v in d.items()))

    print("\n" + "=" * 78)
    print("【device_info】")
    for d in db["device_info"].find({}).limit(3):
        print("  " + " | ".join(f"{k}={str(v)[:50]}" for k, v in d.items()))

    print("\n完成。")


if __name__ == "__main__":
    main()
