# -*- coding: utf-8 -*-
"""
test_cumulative_logic.py - 累积暴露报警（双维度·高暴露小时数·顺应性分层）纯逻辑测试

不依赖 MongoDB / 网络，直接验证 calculator 层：
  1. classify_cumulative_risk 的 L1/L3/L4 边界（ΔP 轨道 + MP 轨道按顺应性分层）
  2. compute_exposure 同时返回瞬时风险与累积维度风险
  3. 「低瞬时、高累积」场景：累积维度独立将评级拉升至 L3（验证缺口已修复）
  4. 高顺应性 / 低顺应性 分层行为验证

用法:
  cd outputs
  PYTHONPATH=. python scripts/test_cumulative_logic.py
"""
import sys
import os
import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lung_protection_cockpit.calculator import (
    classify_cumulative_risk,
    classify_risk,
    compute_exposure,
    enrich_rows,
)

PASS = 0
FAIL = 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}")


def build_rows(mp_per_row, dp_val, vte_ml, step_ms=60000, base_dp_pplat=None):
    """构造等间隔点。mp_per_row 为每行的 MP 值列表（长度=点数）。
    dp_val 恒定 ΔP；vte_ml 控制顺应性 CRS = Vte(L)/ΔP。
    """
    base = datetime.datetime(2026, 1, 1, 0, 0, 0)
    pplat = base_dp_pplat if base_dp_pplat is not None else (dp_val + 5.0)
    rows = []
    for i, mp in enumerate(mp_per_row):
        ts = int(base.timestamp() * 1000) + i * step_ms
        rows.append({
            "ts": ts,
            "dt": base + datetime.timedelta(seconds=step_ms / 1000 * i),
            "PIP": 28.0, "Vte": float(vte_ml), "ftotal": 22.0,
            "Pplat": pplat, "PEEP": 5.0, "DrivePress": dp_val,
        })
    enrich_rows(rows)
    for r in rows:
        r["MP"] = mp_per_row[r["_i"]] if "_i" in r else mp_per_row[rows.index(r)]
    # 用索引方式更稳妥
    for i, r in enumerate(rows):
        r["MP"] = mp_per_row[i]
        r["dP"] = dp_val
    return rows


def rep(val, n):
    return [val] * n


def main():
    print("=" * 64)
    print("  累积暴露报警 · 纯逻辑测试（高暴露小时数 · 顺应性分层）")
    print("=" * 64)

    # 1. classify_cumulative_risk 边界
    print("\n[1] classify_cumulative_risk 边界")
    check("全 0 -> L1", classify_cumulative_risk(0, 0, "high") == 1)
    # ΔP 轨道
    check("ΔP 高暴露 2.0h -> L3", classify_cumulative_risk(2.0, 0, "high") == 3)
    check("ΔP 高暴露 6.0h -> L4", classify_cumulative_risk(6.0, 0, "high") == 4)
    check("ΔP 高暴露 1.9h -> L1", classify_cumulative_risk(1.9, 0, "high") == 1)
    # MP 高顺应性（阈值 18，L3=2h/L4=6h）
    check("MP高 2.0h -> L3", classify_cumulative_risk(0, 2.0, "high") == 3)
    check("MP高 6.0h -> L4", classify_cumulative_risk(0, 6.0, "high") == 4)
    check("MP高 1.9h -> L1", classify_cumulative_risk(0, 1.9, "high") == 1)
    # MP 低顺应性（阈值 20，L3=12h/L4=24h）
    check("MP低 12.0h -> L3", classify_cumulative_risk(0, 12.0, "low") == 3)
    check("MP低 24.0h -> L4", classify_cumulative_risk(0, 24.0, "low") == 4)
    check("MP低 11.9h -> L1", classify_cumulative_risk(0, 11.9, "low") == 1)

    # 2. 低瞬时 + 高累积 -> 累积维度独立将评级拉升至 L3（缺口修复验证）
    print("\n[2] 低瞬时 + 高累积（高顺应性）-> 累积维度 L3")
    # DP=14.9(<15)，MP 仅在 1/10 时刻=18.5（≥18），其余=16.0；Vte=500 -> CRS≈33.6>32.7 高顺应性
    n = 1300
    mp_seq = [18.5 if (i % 10 == 0) else 16.0 for i in range(n)]
    rows = build_rows(mp_seq, dp_val=14.9, vte_ml=500)
    exp = compute_exposure(rows)
    instant = exp["risk_level"]
    cum = exp["cumulative_risk_level"]
    print(f"        instant_risk={instant}  cumulative_risk={cum}  "
          f"mp_over_hours_high={exp['cum_over_minutes']['mp18']/60:.2f}h  "
          f"stratum={exp['compliance_stratum']}  CRS={exp['compliance_mean']:.1f}")
    check("瞬时维度仅轻度升高（L2，MP 峰值略超 17 但占比低）", instant == 2)
    check("累积维度独立升至 L3（MP高暴露≥2h，高顺应性，瞬时仅 L2）", cum >= 3)
    check("合并风险 = max(瞬时, 累积) >= 3", max(instant, cum) >= 3)

    # 3. 高瞬时 + 低累积 -> 瞬时维度主导（L4），累积不拉低
    print("\n[3] 高瞬时 + 低累积 -> 瞬时 L4（max 不被累积拉低）")
    rows2 = build_rows(rep(30.0, 5), dp_val=22.0, vte_ml=500)
    exp2 = compute_exposure(rows2)
    print(f"        instant_risk={exp2['risk_level']}  cumulative_risk={exp2['cumulative_risk_level']}  "
          f"dp_over_hours={exp2['cum_over_minutes']['dp']/60:.3f}h")
    check("瞬时维度为 L4（MP/DP 大幅越限）", exp2["risk_level"] == 4)
    check("合并风险仍为 L4", max(exp2["risk_level"], exp2["cumulative_risk_level"]) == 4)

    # 4. 低顺应性分层：MP≥20 持续 12h -> L3
    print("\n[4] 低顺应性分层（CRS≤32.7，MP≥20 持续 13h -> L3）")
    n4 = 780  # 13h @1min，确保越过 12h 边界
    mp_seq4 = rep(20.5, n4)
    rows4 = build_rows(mp_seq4, dp_val=14.9, vte_ml=400)  # Vte=400 -> CRS≈26.8<32.7 低
    exp4 = compute_exposure(rows4)
    print(f"        cumulative_risk={exp4['cumulative_risk_level']}  "
          f"mp_over_hours_low={exp4['cum_over_minutes']['mp20']/60:.2f}h  "
          f"stratum={exp4['compliance_stratum']}  CRS={exp4['compliance_mean']:.1f}")
    check("低顺应性：MP≥20 持续 13h -> 累积 L3", exp4["cumulative_risk_level"] >= 3)

    # 5. classify_risk 单值维度独立性
    print("\n[5] 单值评级函数独立性")
    check("峰值越限、占比0 -> L2", classify_risk(16, 0, 30, 0) == 2)
    check("超阈占比60% -> L4", classify_risk(16, 60, 30, 60) == 4)
    check("全安全 -> L1", classify_risk(10, 0, 5, 0) == 1)

    print("\n" + "=" * 64)
    total = PASS + FAIL
    print(f"  结果: {PASS}/{total} 通过, {FAIL} 失败")
    if FAIL == 0:
        print("  *** 全部通过 ***")
    print("=" * 64)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
