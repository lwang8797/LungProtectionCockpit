# -*- coding: utf-8 -*-
"""
urs_probe_mp_wave.py - 用真实波形验证 URS 首选的 MP 波形积分法（只读）

URS FR-02: "优先使用波形积分 ∫Paw·dV；波形数据不可用时用公式替代"
本脚本核实：wave_data 是否真的能算出 MP，以及与 Gattinoni 公式的差异。

通道定义（来自 wave_config，deviceId=1787816609）：
  2100 = Paw    (cmH₂O)
  2101 = Flow   (LPM)
  2102 = Volume (ml)
  2103 = CO₂
"""

import sys
import os
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pymongo import MongoClient
from lung_protection_cockpit.config import MONGO_URI, MONGO_DB, DEVICE_ID

CH_PAW, CH_FLOW, CH_VOL = 2100, 2101, 2102
CMH2O_TO_J_PER_L = 0.098   # 1 cmH2O·L = 0.098 J


def main():
    minute = sys.argv[1] if len(sys.argv) > 1 else "2026-08-27 20:57"
    dt = datetime.strptime(minute, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
    a = int(dt.timestamp() * 1000)
    b = a + 60000

    cli = MongoClient(MONGO_URI, serverSelectionTimeoutMS=15000)
    db = cli[MONGO_DB]
    w = db["wave_data"]

    docs = list(w.find({"deviceId": DEVICE_ID, "timeStamp": {"$gte": a, "$lte": b}},
                       {"_id": 0}))
    print("=" * 78)
    print(f"波形窗口 {dt.isoformat()} ~ +60s，共 {len(docs):,} 文档")

    ch = {}
    for d in docs:
        ch.setdefault(d.get("waveId"), []).append(
            (int(d["timeStamp"]), [float(x) for x in (d.get("waveValues") or [])],
             [int(x) for x in (d.get("waveInsps") or [])]))
    print(f"通道: {sorted(ch.keys())}")

    def series(wid):
        lst = sorted(ch.get(wid, []))
        ts_, vs, ins = [], [], []
        for t, v, i in lst:
            ts_.append(t)
            vs.extend(v)
            ins.extend(i)
        return ts_, vs, ins

    print("\n" + "-" * 78)
    print("【通道辨识】")
    for wid in sorted(ch.keys()):
        _, vs, ins = series(wid)
        if not vs:
            continue
        insp = sum(1 for i in ins if i) / len(ins) * 100 if ins else 0
        print(f"  waveId={wid:<6} n={len(vs):>5} 值域[{min(vs):>8.2f},{max(vs):>8.2f}] "
              f"振幅={max(vs)-min(vs):>7.2f} 均值={sum(vs)/len(vs):>7.2f} 吸气标记占比={insp:>5.1f}%")

    if CH_PAW not in ch or CH_VOL not in ch:
        print("\n★ 缺少 Paw(2100) 或 Volume(2102)，无法做波形积分")
        return

    ts_p, paw, insp = series(CH_PAW)
    ts_v, vol, _ = series(CH_VOL)
    ts_f, flow, _ = series(CH_FLOW) if CH_FLOW in ch else ([], [], [])
    print(f"\n  Paw 样本 {len(paw)}，Volume 样本 {len(vol)}，Flow 样本 {len(flow)}")

    # ── 呼吸分割：用 waveInsps 上升沿 ──
    breaths = []
    start = None
    for i in range(1, len(insp)):
        if insp[i] and not insp[i - 1]:
            start = i
        elif (not insp[i]) and insp[i - 1] and start is not None:
            breaths.append((start, i))
            start = None
    print(f"\n  检出呼吸周期 {len(breaths)} 次（按 waveInsps 上升沿分割）")
    if not breaths:
        print("  ★ 无吸气标记，无法按呼吸积分")
        return

    # ── 逐呼吸 ∫Paw·dV ──
    print("\n" + "-" * 78)
    print("【逐呼吸波形积分 ∫Paw·dV】")
    print(f"{'#':>3} {'时长s':>6} {'Ppeak':>7} {'Pplat':>7} {'PEEP':>6} {'ΔV(ml)':>8} "
          f"{'功/呼吸J':>9} {'RR':>5} {'MP_wave':>8}")
    n = min(len(ts_p), len(ts_v))
    works = []
    peaks, plats, peeps, dvs = [], [], [], []
    for k, (i0, i1) in enumerate(breaths[:20]):
        if i1 - i0 < 3 or i1 > n:
            continue
        seg_p = paw[i0:i1]
        seg_v = vol[i0:i1]
        # ∫Paw dV  （梯形）
        work = 0.0
        for j in range(1, len(seg_p)):
            dv_l = (seg_v[j] - seg_v[j - 1]) / 1000.0
            work += (seg_p[j] + seg_p[j - 1]) / 2.0 * dv_l
        work_j = work * CMH2O_TO_J_PER_L
        ppeak = max(seg_p)
        peep = min(paw[max(0, i0 - 5):i0]) if i0 >= 5 else min(seg_p)
        # Pplat：吸气末最后 10% 采样的均值（呼气刚开始前的平台）
        tail = seg_p[int(len(seg_p) * 0.9):] or seg_p[-1:]
        pplat = sum(tail) / len(tail)
        dv = max(seg_v) - min(seg_v)
        dur = (ts_p[i1 - 1] - ts_p[i0]) / 1000.0
        rr = 60.0 / (dur + (60.0 / max(len(breaths), 1) - dur)) if dur > 0 else 0
        works.append(work_j)
        peaks.append(ppeak)
        plats.append(pplat)
        peeps.append(peep)
        dvs.append(dv)
        print(f"{k+1:>3} {dur:>6.2f} {ppeak:>7.2f} {pplat:>7.2f} {peep:>6.2f} {dv:>8.1f} "
              f"{work_j:>9.4f} {60.0/(dur+2.0):>5.1f} {work_j*60.0/(dur+2.0):>8.2f}")

    if not works:
        print("  无有效呼吸")
        return

    nbr = len(breaths)
    mp_wave = sum(works) / len(works) * nbr   # 每分钟总功 = 平均单次功 × 呼吸次数
    ppeak_m = sum(peaks) / len(peaks)
    pplat_m = sum(plats) / len(plats)
    peep_m = sum(peeps) / len(peeps)
    vt_m = sum(dvs) / len(dvs)
    rr_m = float(nbr)

    print(f"\n  窗口内呼吸次数（即 RR）: {nbr} /min")
    print(f"  Ppeak={ppeak_m:.2f}  Pplat(波形估)={pplat_m:.2f}  PEEP={peep_m:.2f}  "
          f"ΔV={vt_m:.1f} mL  ΔP静={pplat_m-peep_m:.2f}  ΔP动={ppeak_m-peep_m:.2f}")
    print(f"  ★ MP(波形积分 ∫Paw·dV) = {mp_wave:.2f} J/min")

    # ── 对照：Gattinoni 公式 ──
    mp_std = 0.098 * rr_m * (vt_m / 1000.0) * (ppeak_m - 0.5 * (pplat_m - peep_m))
    mp_dyn = 0.098 * rr_m * (vt_m / 1000.0) * 0.5 * (ppeak_m + peep_m)
    print(f"  MP(Gattinoni 标准式, Pplat 可用) = {mp_std:.2f} J/min")
    print(f"  MP(动态式 0.5×(Ppeak+PEEP))      = {mp_dyn:.2f} J/min")
    print(f"  → 波形 vs 标准式 偏差 {mp_wave - mp_std:+.2f} J/min "
          f"({(mp_wave-mp_std)/max(mp_std,1e-9)*100:+.1f}%)")
    print(f"  → 波形 vs 动态式 偏差 {mp_wave - mp_dyn:+.2f} J/min "
          f"({(mp_wave-mp_dyn)/max(mp_dyn,1e-9)*100:+.1f}%)")

    # ── 对照：同期 measure_param ──
    print("\n" + "-" * 78)
    print("【同期 measure_param 对照】")
    mp_coll = db["measure_param"]
    near = mp_coll.find_one({"deviceId": DEVICE_ID, "timeStamp": {"$gte": a, "$lte": b}})
    if not near:
        ts_near = int(near["timeStamp"]) if near else a
        prev = list(mp_coll.find({"deviceId": DEVICE_ID, "timeStamp": {"$lte": a}},
                                 {"_id": 0}).sort("timeStamp", -1).limit(16))
        nxt = list(mp_coll.find({"deviceId": DEVICE_ID, "timeStamp": {"$gte": b}},
                                {"_id": 0}).sort("timeStamp", 1).limit(16))
        for label, arr in (("之前最近一批", prev), ("之后最近一批", nxt)):
            if not arr:
                continue
            t = arr[0]["timeStamp"]
            print(f"  {label} @ {datetime.fromtimestamp(int(t)/1000, tz=timezone.utc).isoformat()}:")
            names = {101: "PIP", 102: "Pplat", 104: "PEEP", 106: "Vte", 113: "ftotal"}
            for d in arr:
                pid = d.get("paramId")
                if pid in names:
                    print(f"      {names[pid]:<6} = {d.get('value')}")
    else:
        print("  窗口内直接命中批次")

    print("\n完成。")


if __name__ == "__main__":
    main()
