# -*- coding: utf-8 -*-
"""
calculator.py - 计算引擎
ΔP（驱动压）/ MP（机械功率）计算 + 累积暴露指标。
"""

import math
from typing import Optional

from .config import (
    DP_THRESHOLD, MP_THRESHOLD, RISK_LABELS, SAMPLE_INTERVAL_S,
    CUM_ENERGY_L3_KJ, CUM_ENERGY_L4_KJ,
    CUM_MP_AUC_L3_KJ, CUM_MP_AUC_L4_KJ,
    CUM_DP_AUC_L3, CUM_DP_AUC_L4,
)


def calculate_dp(row: dict) -> float:
    """
    计算 ΔP（驱动压）。
    优先用设备直读 DrivePress，否则用 Pplat - PEEP。
    """
    dp = row.get("DrivePress", float("nan"))
    if not math.isnan(dp):
        return dp
    plat = row.get("Pplat", float("nan"))
    peep = row.get("PEEP", float("nan"))
    if not math.isnan(plat) and not math.isnan(peep):
        return plat - peep
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


def compute_exposure(rows: list) -> dict:
    """
    计算累积暴露指标：均值/最大/超阈占比/AUC/累积能量/风险评级。

    参数:
        rows - 已 enrich 过的行列表（含 dP 和 MP 字段）

    返回:
        dict with dp_stats, mp_stats, cumulative, risk_level
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

    # AUC（梯形法）
    dp_auc = 0.0
    mp_auc = 0.0
    cum_energy = 0.0

    if len(rows) >= 2:
        for i in range(1, len(rows)):
            dt_min = (rows[i]["ts"] - rows[i - 1]["ts"]) / 1000 / 60
            if dt_min <= 0 or dt_min > 1:
                continue

            d0, d1 = rows[i - 1].get("dP", float("nan")), rows[i].get("dP", float("nan"))
            if not math.isnan(d0) and not math.isnan(d1):
                e0 = max(0, d0 - DP_THRESHOLD)
                e1 = max(0, d1 - DP_THRESHOLD)
                dp_auc += (e0 + e1) / 2 * dt_min

            m0, m1 = rows[i - 1].get("MP", float("nan")), rows[i].get("MP", float("nan"))
            if not math.isnan(m0) and not math.isnan(m1):
                e0 = max(0, m0 - MP_THRESHOLD)
                e1 = max(0, m1 - MP_THRESHOLD)
                mp_auc += (e0 + e1) / 2 * dt_min
                cum_energy += ((m0 + m1) / 2) * dt_min

    # 风险评级（瞬时单值维度）
    risk = classify_risk(dp_max, dp_over_pct, mp_max, mp_over_pct)

    # 累积维度评级（由累积 AUC / 累积机械能 判定，阈值为 24h 窗口保守默认）
    cum_risk = classify_cumulative_risk(dp_auc, mp_auc, cum_energy)

    return {
        "dp": {
            "max": dp_max, "mean": dp_mean,
            "threshold": DP_THRESHOLD,
            "over_count": len(dp_over), "over_pct": dp_over_pct,
            "auc": dp_auc,
        },
        "mp": {
            "max": mp_max, "mean": mp_mean,
            "threshold": MP_THRESHOLD,
            "over_count": len(mp_over), "over_pct": mp_over_pct,
            "auc": mp_auc,
        },
        "cumulative_energy_j": cum_energy,
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
    风险评级 L1-L4。
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
    cum_dp_auc: float, cum_mp_auc_j: float, cum_energy_j: float,
    vent_min: float = None,
) -> int:
    """
    累积维度风险评级 L1-L4（24h 窗口）。

    依据累积暴露指标是否越过 L3/L4 保守默认阈值：
      - 机械能（kJ）、MP 超阈 AUC（kJ）、ΔP 超阈 AUC（cmH2O·min）
    低于 L3 → L1（累积维度无风险）；越 L3 → L3；越 L4 → L4。
    累积维度仅会提升到 L3/L4，不会单独产生 L2（L2 由瞬时单值维度负责）。

    ⚠ 阈值待临床确认，可在设置页调整（见 config.CUM_*）。
    """
    energy_kj = (cum_energy_j or 0.0) / 1000.0
    mp_auc_kj = (cum_mp_auc_j or 0.0) / 1000.0
    dp_auc = cum_dp_auc or 0.0

    risk = 1
    if (energy_kj >= CUM_ENERGY_L4_KJ
            or mp_auc_kj >= CUM_MP_AUC_L4_KJ
            or dp_auc >= CUM_DP_AUC_L4):
        risk = 4
    elif (energy_kj >= CUM_ENERGY_L3_KJ
            or mp_auc_kj >= CUM_MP_AUC_L3_KJ
            or dp_auc >= CUM_DP_AUC_L3):
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
