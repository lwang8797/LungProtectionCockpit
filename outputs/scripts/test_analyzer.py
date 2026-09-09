# -*- coding: utf-8 -*-
"""
test_analyzer.py - 用生成数据对 analyzer 纯函数做 URS 口径断言测试。

运行： outputs/.venv/Scripts/python.exe scripts/test_analyzer.py
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from datetime import datetime, timezone

import lung_protection_cockpit.analyzer as A
from scripts.gen_urs_testdata import make_series, series_to_rows
from lung_protection_cockpit.calculator import enrich_rows

BASE_ISO = "2026-09-01T00:00:00+00:00"
PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}  {detail}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


def ts_rows(series, key_from="dP"):
    """make_series->rows->enrich，返回带 ts/dp/mp/stratum 的 analyzer 输入行。"""
    rows = series_to_rows(series)
    enrich_rows(rows)
    out = []
    for r in rows:
        out.append({"ts": r["ts"], "dp": r.get("dP"),
                    "mp": r.get("MP"), "crs": r.get("CRS")})
    return out


def main():
    print("======== 1. linear_slope 直线还原 ========")
    base = int(datetime(2026, 9, 1, tzinfo=timezone.utc).timestamp() * 1000)
    xs = [base + i * 60000 for i in range(240)]           # 4h 逐分钟
    ys = [10.0 + 0.5 * (i / 60.0) for i in range(240)]     # β=0.5/h
    beta, *_ = A.linear_slope(xs, ys)
    check("β≈0.50", abs(beta - 0.5) < 0.01, f"β={beta:.4f}")
    ys_neg = [20.0 - 1.0 * (i / 60.0) for i in range(240)]  # β=-1.0/h
    beta2, *_ = A.linear_slope(xs, ys_neg)
    check("β≈-1.00", abs(beta2 + 1.0) < 0.01, f"β={beta2:.4f}")
    b, _, _, n = A.linear_slope([base], [5.0])
    check("单点→None", b is None and n == 1, f"n={n}")

    print("======== 2. sliding_slopes（ramp 场景应得正值斜率） ========")
    ser = make_series("T", BASE_ISO, hours=30, scenario="ramp", seg={"ramp_per_hour": 1.0})
    rows = ts_rows(ser)
    sl = A.sliding_slopes(rows, "dp", windows_min=(60, 360, 1440))
    for wh, info in sl.items():
        check(f"dp 斜率窗{wh}h beta>0", info["beta"] is not None and info["beta"] > 0,
              f"β={info['beta']} n={info['n']} insuff={info['insufficient']}")

    print("======== 3. cusum_track（低基线→平移，应检出上漂移变化点） ========")
    # 直接构造：24h 稳定 ΔP=5（占满 μ0 24h 窗 → μ0≈5），随后升至 16 持续
    base = int(datetime(2026, 9, 1, tzinfo=timezone.utc).timestamp() * 1000)
    n_low, n_high = 24 * 60, 8 * 60
    rows = []
    for i in range(n_low):
        rows.append({"ts": base + i * 60000, "dp": 5.0, "mp": 8.0})
    for j in range(n_high):
        rows.append({"ts": base + (n_low + j) * 60000, "dp": 16.0, "mp": 9.0})
    cu = A.cusum_track(rows, "dp")
    ups = [c for c in cu["change_points"] if c["side"] == "up"]
    check("step 检出≥1 个上漂移变化点", len(ups) >= 1,
          f"mu0={cu['mu0']} h={cu['h']} cps={len(cu['change_points'])} first={ups[0] if ups else None}")
    check("变化点发生于高段(≥24h)", ups and (ups[0]["ts"] - base) / 3600000 >= 24.0,
          f"at {((ups[0]['ts'] if ups else base) - base) / 3600000:.1f}h")

    print("======== 4. cusum 遇 >4h 断流重置 ========")
    # 3h 低 + 5h 断流 + 随后连续高 —— 断流后 μ0 窗被"高段"占据，但验证状态重置不抛错
    rows4 = []
    for i in range(180):
        rows4.append({"ts": base + i * 60000, "dp": 5.0, "mp": 8.0})
    gap_len = 300  # 5h 断流
    seg2 = []
    for j in range(0, 300):   # 断流结束后再上报
        seg2.append({"ts": base + (180 + gap_len + j) * 60000, "dp": 18.0, "mp": 10.0})
    rows_all = rows4 + seg2
    gaps = [(rows_all[i]["ts"] - rows_all[i - 1]["ts"]) / 60000 for i in range(1, len(rows_all))]
    check("生成了>240min断流", max(gaps) > 240, f"max_gap={max(gaps):.0f}min")
    cu4 = A.cusum_track(rows_all, "dp")
    check("断流后 CUSUM 可运行且不崩溃", isinstance(cu4["change_points"], list), f"cps={len(cu4['change_points'])}")

    print("======== 5. DebounceGradeEngine G1 触发 ========")
    # ΔP 从<15 跳≥15 并持续 70min → 应在第 60min 触发 G1
    base = int(datetime(2026, 9, 1, tzinfo=timezone.utc).timestamp() * 1000)
    eng = A.DebounceGradeEngine(stratum="high")
    feed_rows = []
    for i in range(90):
        dp = 16.0 if i >= 20 else 12.0   # 第20min 起 ΔP=16(≥15)
        mp = 14.0
        feed_rows.append({"ts": base + i * 60000, "dp": dp, "mp": mp})
    raise_ev = None
    for r in feed_rows:
        g, ev = eng.feed(r["ts"], r["dp"], r["mp"])
        if ev == "raise":
            raise_ev = (g, r["ts"])
    # 应在 ΔP≥15 持续 60min 后触发 G1（即 t=20+59=79? 从第20min算第60个连续min→t=79min）
    check("70min 超标后触发 G1", raise_ev is not None and raise_ev[0] == A.G1,
          f"event={raise_ev}")
    if raise_ev:
        at_min = (raise_ev[1] - base) / 60000
        check("触发时刻≈第79min(持续60)", 78 <= at_min <= 80, f"at {at_min}min")

    print("======== 6. Debounce 回落 10min 自动解除 ========")
    # 接续：把 ΔP 降回 <15 持续 10min → 从 G1 回 G0（release）
    eng2 = A.DebounceGradeEngine(stratum="high")
    base2 = base + 200 * 60000
    for i in range(90):
        dp = 16.0 if 20 <= i < 85 else 12.0   # 60min 超标后回落
        eng2.feed(base2 + i * 60000, dp, 14.0)
    # 重建以观察 release 时刻
    eng3 = A.DebounceGradeEngine(stratum="high")
    rel_ev = None
    grade_after_release = None
    seq = [(16.0 if 20 <= i < 85 else 12.0) for i in range(150)]  # 持续到第85min后回落
    # 拉长：回落后继续 60min 观察自动降级
    seq = seq + [12.0] * 200
    for i, dp in enumerate(seq):
        g, ev = eng3.feed(base + i * 60000, dp, 14.0)
        if ev == "release":
            rel_ev = (g, i)
    check("回落后发生 release", rel_ev is not None, f"release={rel_ev}")
    if rel_ev:
        # 超标段 20..84 (65min) 后回落，第95min 前后回落10min → release
        check("release 在回落约10min后", 85 <= rel_ev[1] <= 100, f"at min {rel_ev[1]}")
    check("回落维持后回到 G0/G1", eng3.grade <= A.G1, f"final grade={eng3.grade}")

    print("======== 7. <15min 瞬时超标不升 ≥G2（硬地板） ========")
    eng4 = A.DebounceGradeEngine(stratum="high")
    max_g = A.G0
    for i in range(30):
        dp = 22.0 if 10 <= i < 20 else 10.0   # 仅 10min 严重超标(<15)
        g, _ = eng4.feed(base + i * 60000, dp, 12.0)
        max_g = max(max_g, g)
    check("10min 严重超标最高只到 G1", max_g <= A.G1, f"max_grade={max_g}")

    print()
    print(f"==== 结果：{PASS} passed, {FAIL} failed ====")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
