# -*- coding: utf-8 -*-
"""
test_cumulative_logic.py - 累积暴露报警（双维度合并风险）纯逻辑测试

不依赖 MongoDB / 网络，直接验证 calculator 层：
  1. classify_cumulative_risk 的 L1/L3/L4 边界
  2. compute_exposure 同时返回瞬时风险与累积维度风险
  3. 「低瞬时、高累积」场景：累积维度独立将评级拉升至 L3（验证缺口已修复）

用法:
  cd 输出
  PYTHONPATH=. python scripts/test_cumulative_logic.py
"""
import sys
import os
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


def build_rows(n, mp_val, dp_val, step_ms=60000):
    """构造 n 个等间隔点，并强制 MP/dP 为给定值（绕过公式，便于精确控制场景）。"""
    import datetime
    rows = []
    base = datetime.datetime(2026, 1, 1, 0, 0, 0)
    for i in range(n):
        ts = int(base.timestamp() * 1000) + i * step_ms
        rows.append({
            "ts": ts,
            "dt": base + datetime.timedelta(seconds=step_ms / 1000 * i),
            "PIP": 28.0, "Vte": 420.0, "ftotal": 22.0,
            "Pplat": 20.0, "PEEP": 5.0, "DrivePress": dp_val,
        })
    enrich_rows(rows)
    for r in rows:
        r["MP"] = mp_val
        r["dP"] = dp_val
    return rows


def main():
    print("=" * 60)
    print("  累积暴露报警 · 纯逻辑测试")
    print("=" * 60)

    # 1. classify_cumulative_risk 边界
    print("\n[1] classify_cumulative_risk 边界")
    check("全 0 -> L1", classify_cumulative_risk(0, 0, 0) == 1)
    check("机械能 50kJ -> L3", classify_cumulative_risk(0, 0, 50000) == 3)
    check("机械能 100kJ -> L4", classify_cumulative_risk(0, 0, 100000) == 4)
    check("机械能 49.9kJ -> L1(低于L3)", classify_cumulative_risk(0, 0, 49900) == 1)
    check("ΔP AUC 1500 -> L3", classify_cumulative_risk(1500, 0, 0) == 3)
    check("ΔP AUC 3000 -> L4", classify_cumulative_risk(3000, 0, 0) == 4)
    check("ΔP AUC 1499 -> L1", classify_cumulative_risk(1499, 0, 0) == 1)
    check("MP AUC 1.5kJ(1500J) -> L3", classify_cumulative_risk(0, 1500, 0) == 3)
    check("MP AUC 3.0kJ(3000J) -> L4", classify_cumulative_risk(0, 3000, 0) == 4)
    check("MP AUC 1.49kJ -> L1", classify_cumulative_risk(0, 1490, 0) == 1)

    # 2. 高累积 / 低瞬时 场景：累积维度独立拉升评级
    print("\n[2] 低瞬时 + 高累积 -> 累积维度 L3（缺口修复验证）")
    # MP=16.5（<17 单值安全），DP=14（<15 单值安全），持续 ~51.6h
    rows = build_rows(3100, mp_val=16.5, dp_val=14.0)
    exp = compute_exposure(rows)
    instant = exp["risk_level"]
    cum = exp["cumulative_risk_level"]
    print(f"        instant_risk={instant}  cumulative_risk={cum}  energy_j={round(exp['cumulative_energy_j'])}")
    check("瞬时维度为 L1（单值均低于阈值）", instant == 1)
    check("累积维度升至 L3（机械能越 L3）", cum >= 3)
    check("合并风险 = max(瞬时, 累积) >= 3", max(instant, cum) >= 3)

    # 3. 反向：高瞬时 / 低累积 不应被累积维度拉低（仍由瞬时决定）
    print("\n[3] 高瞬时 + 低累积 -> 瞬时维度主导（L4）")
    rows2 = build_rows(5, mp_val=30.0, dp_val=22.0)  # 短时强越限
    exp2 = compute_exposure(rows2)
    print(f"        instant_risk={exp2['risk_level']}  cumulative_risk={exp2['cumulative_risk_level']}")
    check("瞬时维度为 L4（MP/DP 大幅越限）", exp2["risk_level"] == 4)
    check("合并风险仍为 L4（max 不被累积拉低）", max(exp2["risk_level"], exp2["cumulative_risk_level"]) == 4)

    # 4. classify_risk 单值维度不被污染
    print("\n[4] 单值评级函数独立性")
    # classify_risk 的 L3/L4 由「超阈占比」决定（设计如此），峰值大但占比低 -> L2
    check("classify_risk 峰值越限、占比0 -> L2", classify_risk(16, 0, 30, 0) == 2)
    check("classify_risk 超阈占比60% -> L4", classify_risk(16, 60, 30, 60) == 4)
    check("classify_risk 全安全 -> L1", classify_risk(10, 0, 5, 0) == 1)

    print("\n" + "=" * 60)
    total = PASS + FAIL
    print(f"  结果: {PASS}/{total} 通过, {FAIL} 失败")
    if FAIL == 0:
        print("  *** 全部通过 ***")
    print("=" * 60)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
