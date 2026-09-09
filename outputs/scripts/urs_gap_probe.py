# -*- coding: utf-8 -*-
"""
urs_gap_probe.py - 针对 URS 新需求的「数据可行性探针」（只读，不写库）

目的：在动手改代码前，用真实数据核实
  1) 各参数（Pplat/PEEP/PIP/Vte/ftotal/DrivePress...）的实际可用率与取值范围
  2) 上报节奏（每分钟几条？间隔多少秒？）——决定 Δt / 有效通气时长 / 降采样口径
  3) ΔP 三种算法（Pplat-PEEP / DrivePress / PIP-PEEP）差异
  4) MP 标准式 vs 动态式差异
  5) 现有 compute_exposure 中「dt_min > 1 就跳过」是否会把 AUC/TAT 全部抹成 0
"""

import sys
import os
import math
from collections import defaultdict
from datetime import datetime, timezone

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


def fmt(v, nd=2):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "NaN"
    return f"{v:.{nd}f}"


def main():
    hours = float(sys.argv[1]) if len(sys.argv) > 1 else 6.0
    cli = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    db = cli[MONGO_DB]
    coll = db[COLL_RAW]

    newest = coll.find_one({"deviceId": DEVICE_ID}, sort=[("timeStamp", -1)])
    oldest = coll.find_one({"deviceId": DEVICE_ID}, sort=[("timeStamp", 1)])
    if not newest:
        print("无数据")
        return
    end_ts = int(newest["timeStamp"])
    start_ts = end_ts - int(hours * 3600 * 1000)

    print("=" * 78)
    print(f"设备 {DEVICE_ID}  窗口 {hours}h")
    print(f"  全库最早 {datetime.fromtimestamp(int(oldest['timeStamp'])/1000, tz=timezone.utc).isoformat()}")
    print(f"  全库最晚 {datetime.fromtimestamp(end_ts/1000, tz=timezone.utc).isoformat()}")
    print(f"  窗口起   {datetime.fromtimestamp(start_ts/1000, tz=timezone.utc).isoformat()}")

    # 1) 采样窗口内的全部文档（只取需要的字段）
    docs = list(coll.find(
        {"deviceId": DEVICE_ID, "timeStamp": {"$gte": start_ts, "$lte": end_ts}},
        {"_id": 0, "paramId": 1, "value": 1, "timeStamp": 1, "unitName": 1, "name": 1},
    ))
    print(f"  窗口内文档数 {len(docs)}")

    # 2) paramId 覆盖情况
    by_pid = defaultdict(list)
    for d in docs:
        by_pid[d["paramId"]].append(d)
    print("\n" + "-" * 78)
    print("【1】paramId 覆盖情况（窗口内）")
    print(f"{'pid':>5} {'名称':<12} {'条数':>7} {'有效数':>7} {'可用率':>7}  {'单位':<12} 样例/范围")
    for pid in sorted(by_pid.keys()):
        lst = by_pid[pid]
        vals = [to_float(d.get("value")) for d in lst]
        ok = [v for v in vals if not math.isnan(v)]
        name = PARAM_MAP.get(pid, lst[0].get("name") or "?")
        unit = lst[0].get("unitName") or ""
        rate = len(ok) / len(lst) * 100 if lst else 0
        rng = ""
        if ok:
            rng = f"min={min(ok):.2f} max={max(ok):.2f} mean={sum(ok)/len(ok):.2f}"
        else:
            sample = [str(d.get("value")) for d in lst[:3]]
            rng = "样例=" + ",".join(sample)
        print(f"{pid:>5} {str(name):<12} {len(lst):>7} {len(ok):>7} {rate:>6.1f}%  {unit:<12} {rng}")

    # 3) 上报节奏
    print("\n" + "-" * 78)
    print("【2】上报节奏（决定 Δt / 有效通气时长 / 是否需要降采样）")
    tss = sorted(set(int(d["timeStamp"]) for d in docs))
    print(f"  窗口内不同时间戳个数: {len(tss)}")
    if len(tss) >= 2:
        gaps = [(tss[i] - tss[i-1]) / 1000.0 for i in range(1, len(tss))]
        gaps_sorted = sorted(gaps)
        def pct(p):
            return gaps_sorted[min(len(gaps_sorted) - 1, int(len(gaps_sorted) * p))]
        print(f"  相邻间隔(s): min={min(gaps):.1f} p50={pct(0.5):.1f} p90={pct(0.9):.1f} "
              f"p99={pct(0.99):.1f} max={max(gaps):.1f} mean={sum(gaps)/len(gaps):.1f}")
        span_h = (tss[-1] - tss[0]) / 3600000.0
        print(f"  时间跨度: {span_h:.3f} h；平均每整分钟样本 {len(tss)/max(span_h,1e-9)/60:.2f} 个")
        # 每分钟样本数分布
        per_min = defaultdict(int)
        for t in tss:
            per_min[t // 60000] += 1
        cnt = sorted(per_min.values())
        print(f"  有数据的整分钟数: {len(per_min)}；每分钟样本数 min={cnt[0]} p50={cnt[len(cnt)//2]} max={cnt[-1]}")
        # 缺数据分钟（断流）
        minutes_total = int((tss[-1] - tss[0]) / 60000) + 1
        print(f"  理论分钟数: {minutes_total}，缺口分钟数: {minutes_total - len(per_min)} "
              f"→ 数据完整率 DCR = {len(per_min)/minutes_total*100:.1f}%")
        # 最大断流
        big = [g for g in gaps if g > 240]
        print(f"  >4min 的间隔个数: {len(big)}" + (f"，最大 {max(big)/60:.1f} min" if big else ""))
        print(f"  >1min 的间隔个数: {len([g for g in gaps if g > 60])}")

    # 4) 逐行透视
    print("\n" + "-" * 78)
    print("【3】逐行透视后 ΔP / MP 三种算法对比（前 12 行 + 统计）")
    rows = {}
    for d in docs:
        pid = d["paramId"]
        if pid not in PARAM_MAP:
            continue
        rows.setdefault(int(d["timeStamp"]), {})[PARAM_MAP[pid]] = to_float(d.get("value"))
    sorted_ts = sorted(rows.keys())
    pivoted = []
    for ts in sorted_ts:
        r = rows[ts]
        r["ts"] = ts
        pivoted.append(r)

    def dp_static(r):
        p, e = r.get("Pplat", float("nan")), r.get("PEEP", float("nan"))
        return p - e if (not math.isnan(p) and not math.isnan(e)) else float("nan")

    def dp_dev(r):
        return r.get("DrivePress", float("nan"))

    def dp_dyn(r):
        p, e = r.get("PIP", float("nan")), r.get("PEEP", float("nan"))
        return p - e if (not math.isnan(p) and not math.isnan(e)) else float("nan")

    def mp_std(r, dp):
        rr, vt, pip = r.get("ftotal", float("nan")), r.get("Vte", float("nan")), r.get("PIP", float("nan"))
        if any(math.isnan(x) for x in (rr, vt, pip, dp)):
            return float("nan")
        return 0.098 * rr * (vt / 1000.0) * (pip - 0.5 * dp)

    def mp_dyn(r):
        rr, vt, pip, e = (r.get("ftotal", float("nan")), r.get("Vte", float("nan")),
                          r.get("PIP", float("nan")), r.get("PEEP", float("nan")))
        if any(math.isnan(x) for x in (rr, vt, pip, e)):
            return float("nan")
        return 0.098 * rr * (vt / 1000.0) * 0.5 * (pip + e)

    print(f"{'时间(UTC)':<20} {'Pplat':>6} {'PEEP':>5} {'PIP':>6} {'Vte':>6} {'RR':>5} "
          f"{'ΔP静':>6} {'ΔPdev':>6} {'ΔP动':>6} {'MP静':>7} {'MP动':>7}")
    for r in pivoted[:12]:
        t = datetime.fromtimestamp(r["ts"] / 1000, tz=timezone.utc).strftime("%m-%d %H:%M:%S")
        ds, dd, dn = dp_static(r), dp_dev(r), dp_dyn(r)
        print(f"{t:<20} {fmt(r.get('Pplat'),1):>6} {fmt(r.get('PEEP'),1):>5} {fmt(r.get('PIP'),1):>6} "
              f"{fmt(r.get('Vte'),0):>6} {fmt(r.get('ftotal'),0):>5} {fmt(ds,1):>6} {fmt(dd,1):>6} {fmt(dn,1):>6} "
              f"{fmt(mp_std(r, ds),2):>7} {fmt(mp_dyn(r),2):>7}")

    # 统计可用率
    n = len(pivoted)
    def avail(f):
        return sum(1 for r in pivoted if not math.isnan(f(r))) / n * 100 if n else 0
    print(f"\n  行数 {n}")
    print(f"  ΔP 静态(Pplat-PEEP) 可用率 {avail(dp_static):.1f}%")
    print(f"  ΔP 设备直读(160)    可用率 {avail(dp_dev):.1f}%")
    print(f"  ΔP 动态(PIP-PEEP)   可用率 {avail(dp_dyn):.1f}%")
    print(f"  MP 依赖(ftotal&Vte&PIP) 可用率 "
          f"{sum(1 for r in pivoted if not any(math.isnan(r.get(k, float('nan'))) for k in ('ftotal','Vte','PIP')))/n*100:.1f}%")

    # ΔP 三法差异
    pairs = [(dp_static(r), dp_dev(r)) for r in pivoted
             if not math.isnan(dp_static(r)) and not math.isnan(dp_dev(r))]
    if pairs:
        diffs = [a - b for a, b in pairs]
        print(f"  ΔP静 vs ΔPdev: n={len(pairs)} 平均差 {sum(diffs)/len(diffs):+.2f} "
              f"(范围 {min(diffs):+.2f} ~ {max(diffs):+.2f})")
    pairs2 = [(dp_static(r), dp_dyn(r)) for r in pivoted
              if not math.isnan(dp_static(r)) and not math.isnan(dp_dyn(r))]
    if pairs2:
        diffs = [a - b for a, b in pairs2]
        print(f"  ΔP静 vs ΔP动 : n={len(pairs2)} 平均差 {sum(diffs)/len(diffs):+.2f} "
              f"(范围 {min(diffs):+.2f} ~ {max(diffs):+.2f})")

    # 5) 现有 compute_exposure 的 dt 门限影响
    print("\n" + "-" * 78)
    print("【4】现有 compute_exposure 的「dt_min>1 跳过」影响评估")
    kept = skipped = 0
    kept_span = skipped_span = 0.0
    for i in range(1, len(pivoted)):
        dt_min = (pivoted[i]["ts"] - pivoted[i-1]["ts"]) / 1000 / 60
        if dt_min <= 0 or dt_min > 1:
            skipped += 1
            skipped_span += min(dt_min, 60)
        else:
            kept += 1
            kept_span += dt_min
    print(f"  参与积分的区间 {kept} 段，覆盖 {kept_span:.2f} min")
    print(f"  被跳过的区间 {skipped} 段，覆盖 {skipped_span:.2f} min（其中超 60min 的按 60min 计）")
    if kept + skipped:
        print(f"  → 时间覆盖率仅 {kept_span/(kept_span+skipped_span)*100:.1f}%，"
              f"即现有 AUC / TAT / 有效通气时长会严重低估")

    # 6) 若改为「按真实间隔积分（上限 4h 前向填充）」的估算
    print("\n" + "-" * 78)
    print("【5】按「真实间隔积分 + ≤4h 前向填充」重算的暴露量（模拟 URS 口径）")
    FF_LIMIT_MIN = 240.0
    tat_dp = auc_dp = tat_mp = auc_mp = 0.0
    vent_min = gap_min = 0.0
    n_gap_over4h = 0
    for i in range(1, len(pivoted)):
        dt = (pivoted[i]["ts"] - pivoted[i-1]["ts"]) / 1000 / 60
        if dt <= 0:
            continue
        if dt > FF_LIMIT_MIN:
            n_gap_over4h += 1
            gap_min += dt
            dt_eff = FF_LIMIT_MIN     # 前向填充上限 4h，其余记为断流
        else:
            dt_eff = dt
        d0, d1 = dp_static(pivoted[i-1]), dp_static(pivoted[i])
        m0, m1 = mp_std(pivoted[i-1], d0), mp_std(pivoted[i], d1)
        if math.isnan(d0):
            d0 = dp_dyn(pivoted[i-1])
            m0 = mp_dyn(pivoted[i-1])
        if math.isnan(d1):
            d1 = dp_dyn(pivoted[i])
            m1 = mp_dyn(pivoted[i])
        if not (math.isnan(d0) or math.isnan(d1)):
            vent_min += dt_eff
            if (d0 >= 15) and (d1 >= 15):
                tat_dp += dt_eff
            elif (d0 >= 15) != (d1 >= 15):
                tat_dp += dt_eff / 2
            auc_dp += (max(0.0, d0 - 15) + max(0.0, d1 - 15)) / 2 * dt_eff
        if not (math.isnan(m0) or math.isnan(m1)):
            if (m0 >= 17) and (m1 >= 17):
                tat_mp += dt_eff
            elif (m0 >= 17) != (m1 >= 17):
                tat_mp += dt_eff / 2
            auc_mp += (max(0.0, m0 - 17) + max(0.0, m1 - 17)) / 2 * dt_eff
    print(f"  有效通气时长      {vent_min:.1f} min = {vent_min/60:.2f} h")
    print(f"  ΔP TAT(≥15)       {tat_dp:.1f} min = {tat_dp/60:.2f} h")
    print(f"  ΔP AUC(≥15)       {auc_dp:.1f} cmH2O·min = {auc_dp/60:.2f} cmH2O·h")
    print(f"  MP TAT(≥17)       {tat_mp:.1f} min = {tat_mp/60:.2f} h")
    print(f"  MP AUC(≥17)       {auc_mp:.1f} J·min/min = {auc_mp/60:.2f} J·h/min")
    print(f"  ΔP PTA            {(tat_dp/vent_min*100 if vent_min else 0):.1f}%")
    print(f"  MP PTA            {(tat_mp/vent_min*100 if vent_min else 0):.1f}%")
    print(f"  >4h 断流段数 {n_gap_over4h}，累计断流 {gap_min:.1f} min")

    # 7) 现有 metrics_1min 里的值对照
    print("\n" + "-" * 78)
    print("【6】metrics_1min 现状对照（最近 5 条）")
    m1 = list(db[COLL_1MIN].find({"deviceId": DEVICE_ID}, {"_id": 0})
              .sort("minute", -1).limit(5))
    if not m1:
        print("  （无聚合数据）")
    for d in m1:
        print("  " + " | ".join([
            d.get("minuteISO", "?")[:19],
            f"vent={d.get('vent_points')}",
            f"dp_mean={fmt(d.get('dp_mean'),1)}",
            f"mp_mean={fmt(d.get('mp_mean'),2)}",
            f"cum_dp_over_min={fmt(d.get('cum_dp_over_min'),3)}",
            f"cum_mp18_min={fmt(d.get('cum_mp_over_min_18'),3)}",
            f"cum_dp_auc={fmt(d.get('cum_dp_auc_above'),3)}",
            f"vent_dur_min={fmt(d.get('vent_duration_min'),2)}",
            f"crs={fmt(d.get('compliance_mean'),1)}",
        ]))
    print("\n完成。")


if __name__ == "__main__":
    main()
