# -*- coding: utf-8 -*-
"""
calculator.py - 计算引擎
ΔP（驱动压）/ MP（机械功率）计算 + 累积暴露指标。
"""

import math
from typing import Optional

from .config import (
    DP_THRESHOLD, MP_THRESHOLD, RISK_LABELS, SAMPLE_INTERVAL_S,
    COMPLIANCE_STRATUM_THRESHOLD,
    CUM_DP_OVER_HOURS_L3, CUM_DP_OVER_HOURS_L4,
    MP_HIGH_STRATUM_THRESHOLD, MP_LOW_STRATUM_THRESHOLD,
    CUM_MP_OVER_HOURS_L3_HIGH, CUM_MP_OVER_HOURS_L4_HIGH,
    CUM_MP_OVER_HOURS_L3_LOW, CUM_MP_OVER_HOURS_L4_LOW,
)


def calculate_dp(row: dict) -> float:
    """
    计算 ΔP（驱动压）。
    优先静态 ΔP = Pplat - PEEP（文献首选），回退设备直读 DrivePress（动态），
    再回退动态 ΔP = Ppeak - PEEP。
    """
    plat = row.get("Pplat", float("nan"))
    peep = row.get("PEEP", float("nan"))
    if not math.isnan(plat) and not math.isnan(peep):
        return plat - peep
    dp = row.get("DrivePress", float("nan"))
    if not math.isnan(dp):
        return dp
    pip = row.get("PIP", float("nan"))
    if not math.isnan(pip) and not math.isnan(peep):
        return pip - peep
    return float("nan")


def calculate_mp(row: dict, dp: Optional[float] = None) -> float:
    """
    计算 MP（机械功率）。
    MP = 0.098 × RR × VT_L × (PIP - 0.5 × ΔP)

    RR 优先用 ftotal（总频率），回退 PR。
    """
    if dp is None:
        dp = calculate_dp(row)

    rr = row.get("ftotal", float("nan"))
    if math.isnan(rr):
        rr = row.get("PR", float("nan"))

    vte = row.get("Vte", float("nan"))   # mL
    pip = row.get("PIP", float("nan"))

    if any(math.isnan(x) for x in [rr, vte, pip, dp]):
        return float("nan")

    vt_l = vte / 1000.0
    return 0.098 * rr * vt_l * (pip - 0.5 * dp)


def enrich_rows(rows: list) -> list:
    """
    给已采集的行列表添加 dP 和 MP 字段。
    原地修改并返回。
    """
    for r in rows:
        r["dP"] = calculate_dp(r)
        r["MP"] = calculate_mp(r, r["dP"])
    return rows


def filter_ventilated(rows: list) -> tuple:
    """
    过滤待机行（ΔP <= 0），返回 (vent_rows, standby_rows)。
    """
    vent = [r for r in rows if not math.isnan(r.get("dP", float("nan"))) and r["dP"] > 0]
    standby = [r for r in rows if math.isnan(r.get("dP", float("nan"))) or r["dP"] <= 0]
    return vent, standby


def _above_minutes(v0, v1, dt_min, thr):
    """区间 [v0,v1]（时长 dt_min 分钟）内"值≥thr"的分钟数（梯形风格）。"""
    if math.isnan(v0) or math.isnan(v1):
        return 0.0
    if v0 >= thr and v1 >= thr:
        return dt_min
    if v0 < thr and v1 < thr:
        return 0.0
    return dt_min / 2.0


def _auc_above(v0, v1, dt_min, thr):
    """区间 [v0,v1]（时长 dt_min 分钟）内 (值−thr) 的梯形 AUC。"""
    if math.isnan(v0) or math.isnan(v1):
        return 0.0
    e0, e1 = max(0.0, v0 - thr), max(0.0, v1 - thr)
    return (e0 + e1) / 2.0 * dt_min


def compute_exposure(rows: list) -> dict:
    """
    计算暴露指标与累积暴露（双轨制：ΔP 轨道 + MP 轨道，按文献精读）。

    累积暴露度量：高暴露分钟数（→高暴露小时数）、超阈 AUC、总机械能、顺应性。
    参数 rows 为已 enrich 过的行（含 dP / MP 字段）。
    """
    dp_vals = [r["dP"] for r in rows if not math.isnan(r.get("dP", float("nan")))]
    mp_vals = [r["MP"] for r in rows if not math.isnan(r.get("MP", float("nan")))]

    dp_max = max(dp_vals) if dp_vals else float("nan")
    dp_mean = sum(dp_vals) / len(dp_vals) if dp_vals else float("nan")
    dp_over = [v for v in dp_vals if v > DP_THRESHOLD]
    dp_over_pct = (len(dp_over) / len(dp_vals) * 100) if dp_vals else 0.0

    mp_max = max(mp_vals) if mp_vals else float("nan")
    mp_mean = sum(mp_vals) / len(mp_vals) if mp_vals else float("nan")
    mp_over = [v for v in mp_vals if v > MP_THRESHOLD]
    mp_over_pct = (len(mp_over) / len(mp_vals) * 100) if mp_vals else 0.0

    # ── 累积暴露（高暴露分钟数 / 超阈 AUC / 总机械能 / 顺应性）──
    dp_over_min = mp_over_min_17 = mp_over_min_18 = mp_over_min_20 = 0.0
    dp_auc_above = mp_auc_above_17 = mp_auc_above_18 = mp_auc_above_20 = 0.0
    cum_energy = 0.0
    comp_sum = 0.0
    comp_n = 0

    if len(rows) >= 2:
        for i in range(1, len(rows)):
            dt_min = (rows[i]["ts"] - rows[i - 1]["ts"]) / 1000 / 60
            if dt_min <= 0 or dt_min > 1:
                continue

            d0, d1 = rows[i - 1].get("dP", float("nan")), rows[i].get("dP", float("nan"))
            m0, m1 = rows[i - 1].get("MP", float("nan")), rows[i].get("MP", float("nan"))

            if not math.isnan(d0) and not math.isnan(d1):
                dp_over_min += _above_minutes(d0, d1, dt_min, DP_THRESHOLD)
                dp_auc_above += _auc_above(d0, d1, dt_min, DP_THRESHOLD)

            if not math.isnan(m0) and not math.isnan(m1):
                mp_over_min_17 += _above_minutes(m0, m1, dt_min, MP_THRESHOLD)
                mp_over_min_18 += _above_minutes(m0, m1, dt_min, MP_HIGH_STRATUM_THRESHOLD)
                mp_over_min_20 += _above_minutes(m0, m1, dt_min, MP_LOW_STRATUM_THRESHOLD)
                mp_auc_above_17 += _auc_above(m0, m1, dt_min, MP_THRESHOLD)
                mp_auc_above_18 += _auc_above(m0, m1, dt_min, MP_HIGH_STRATUM_THRESHOLD)
                mp_auc_above_20 += _auc_above(m0, m1, dt_min, MP_LOW_STRATUM_THRESHOLD)
                cum_energy += ((m0 + m1) / 2.0) * dt_min

            # 顺应性 CRS = VT(mL) / ΔP（Lijovic 2026），单位 mL/cmH2O，与分层阈值 32.7 一致
            vt = rows[i].get("Vte", float("nan"))
            if not math.isnan(vt) and not math.isnan(d1) and d1 > 0:
                comp_sum += vt / d1
                comp_n += 1

    compliance_mean = (comp_sum / comp_n) if comp_n else float("nan")

    # 风险评级（瞬时单值维度）
    risk = classify_risk(dp_max, dp_over_pct, mp_max, mp_over_pct)

    # 累积维度评级（高暴露小时数 + 顺应性分层）
    dp_over_hours = dp_over_min / 60.0
    stratum = "high" if (not math.isnan(compliance_mean)
                         and compliance_mean > COMPLIANCE_STRATUM_THRESHOLD) else "low"
    mp_over_hours = (mp_over_min_18 / 60.0) if stratum == "high" else (mp_over_min_20 / 60.0)
    cum_risk = classify_cumulative_risk(dp_over_hours, mp_over_hours, stratum)

    return {
        "dp": {
            "max": dp_max, "mean": dp_mean,
            "threshold": DP_THRESHOLD,
            "over_count": len(dp_over), "over_pct": dp_over_pct,
            "auc": dp_auc_above,
        },
        "mp": {
            "max": mp_max, "mean": mp_mean,
            "threshold": MP_THRESHOLD,
            "over_count": len(mp_over), "over_pct": mp_over_pct,
            "auc": mp_auc_above_17,
        },
        # 原始累积累加量（供聚合层滚动）
        "cum_over_minutes": {
            "dp": dp_over_min, "mp17": mp_over_min_17,
            "mp18": mp_over_min_18, "mp20": mp_over_min_20,
        },
        "cum_auc_above": {
            "dp": dp_auc_above, "mp17": mp_auc_above_17,
            "mp18": mp_auc_above_18, "mp20": mp_auc_above_20,
        },
        "cum_energy_j": cum_energy,
        "compliance_mean": compliance_mean,
        "compliance_stratum": stratum,
        "risk_level": risk,
        "risk_label": RISK_LABELS.get(risk, "L1 正常"),
        "cumulative_risk_level": cum_risk,
        "cumulative_risk_label": RISK_LABELS.get(cum_risk, "L1 正常"),
        "vent_points": len(dp_vals),
    }


def classify_risk(
    dp_max: float, dp_over_pct: float,
    mp_max: float, mp_over_pct: float,
) -> int:
    """
    风险评级 L1-L4（瞬时单值维度）。
    L1 正常 | L2 关注（超阈但少） | L3 警告（超阈>20%） | L4 危险（超阈>50%）
    """
    risk = 1
    if (dp_max is not None and not math.isnan(dp_max) and dp_max > DP_THRESHOLD) or \
       (mp_max is not None and not math.isnan(mp_max) and mp_max > MP_THRESHOLD):
        risk = max(risk, 2)
    if dp_over_pct > 20 or mp_over_pct > 20:
        risk = max(risk, 3)
    if dp_over_pct > 50 or mp_over_pct > 50:
        risk = max(risk, 4)
    return risk


def classify_cumulative_risk(
    dp_over_hours: float, mp_over_hours: float, stratum: str = "high",
) -> int:
    """
    累积维度风险评级 L1/L3/L4（双轨制·基于高暴露小时数）。

    - ΔP 轨道：高暴露小时数 ≥ CUM_DP_OVER_HOURS_L3 → L3；≥ L4 → L4。
    - MP 轨道：按顺应性分层选用对应阈值
        · 高顺应性（CRS>32.7）：MP≥18 且 高暴露≥2h → L3/L4
        · 低顺应性（CRS≤32.7）：MP≥20 且 高暴露≥12h → L3/L4
    累积维度仅提升到 L3/L4（不会单独产生 L2，L2 由瞬时单值维度负责）。

    阈值依据：Lijovic 2026（高顺应性 MP≥18×2h 显著累积伤害；低顺应性窄带）；
    ΔP ≥2h 类推自同文献。仍待临床最终确认（可在设置页调整）。
    """
    if stratum == "low":
        l3, l4 = CUM_MP_OVER_HOURS_L3_LOW, CUM_MP_OVER_HOURS_L4_LOW
    else:
        l3, l4 = CUM_MP_OVER_HOURS_L3_HIGH, CUM_MP_OVER_HOURS_L4_HIGH

    dp_h = dp_over_hours or 0.0
    mp_h = mp_over_hours or 0.0

    risk = 1
    if dp_h >= CUM_DP_OVER_HOURS_L4 or mp_h >= l4:
        risk = 4
    elif dp_h >= CUM_DP_OVER_HOURS_L3 or mp_h >= l3:
        risk = 3
    return risk


def build_risk_map_points(rows: list) -> list:
    """
    构建二维风险图散点数据：[(dP, MP, ts), ...]
    用于前端 ΔP-MP 散点图。
    """
    points = []
    for r in rows:
        dp = r.get("dP", float("nan"))
        mp = r.get("MP", float("nan"))
        if not math.isnan(dp) and not math.isnan(mp):
            points.append({
                "dp": round(dp, 1),
                "mp": round(mp, 2),
                "ts": r["ts"],
                "dt": r["dt"].isoformat() if hasattr(r.get("dt"), "isoformat") else "",
            })
    return points
