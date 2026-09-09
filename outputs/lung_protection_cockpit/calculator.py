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
    VT_SOURCE_KEYS, RR_SOURCE_KEYS, PPLAT_STRICT_ORDER,
    MAX_FORWARD_FILL_MIN, DCR_LOW_THRESHOLD,
)

NAN = float("nan")


# ════════════════════ 参数解析口径（2026-09-09 用户确认）════════════════════
#   RR  : 固定 ftotal(113)
#   VT  : Vti(110) 优先，回退 Vte(106)
#   Pplat: 不无条件信任 —— 需 PEEP < Pplat < Ppeak 才算静态，否则降级动态

def _num(row: dict, key: str) -> float:
    """安全取数值字段，缺失/非数值 → NaN"""
    try:
        v = float(row.get(key, NAN))
    except (TypeError, ValueError):
        return NAN
    return v


def resolve_vt(row: dict) -> float:
    """潮气量 Vt（mL）：Vti(110) 优先，回退 Vte(106)。"""
    for key in VT_SOURCE_KEYS:
        v = _num(row, key)
        if not math.isnan(v) and v > 0:
            return v
    return NAN


def resolve_rr(row: dict) -> float:
    """呼吸频率 RR（bpm）：固定用 ftotal(113)（总频率）。"""
    for key in RR_SOURCE_KEYS:
        v = _num(row, key)
        if not math.isnan(v) and v > 0:
            return v
    return NAN


def plat_is_credible(row: dict) -> bool:
    """平台压 Pplat 是否可信（用户确认：Ppeak 更可信，Pplat 需通过合理性校验）。

    判据：PEEP < Pplat < Ppeak。
    实测曾出现 Pplat == Ppeak 的批次（疑似未真正执行吸气末保持），此类必须降级。
    """
    peep = _num(row, "PEEP")
    plat = _num(row, "Pplat")
    pip = _num(row, "PIP")
    if math.isnan(peep) or math.isnan(plat) or math.isnan(pip):
        return False
    if not (plat > peep):
        return False
    if PPLAT_STRICT_ORDER and not (plat < pip):
        return False
    return True


def calc_dp(row: dict) -> tuple:
    """驱动压 ΔP，返回 (值, 来源)。来源：'static' / 'dynamic' / 'none'。

    - 静态 ΔP = Pplat − PEEP（仅在 Pplat 通过可信性校验时）
    - 动态 ΔP = Ppeak − PEEP（降级，界面需打 *Dyn* 标记）
    """
    if plat_is_credible(row):
        return _num(row, "Pplat") - _num(row, "PEEP"), "static"

    pip, peep = _num(row, "PIP"), _num(row, "PEEP")
    if not (math.isnan(pip) or math.isnan(peep)):
        v = pip - peep
        if v > 0:
            return v, "dynamic"
    return NAN, "none"


def calc_mp(row: dict, dp: Optional[float] = None, dp_source: Optional[str] = None) -> tuple:
    """机械功率 MP（J/min），返回 (值, 来源)。

    - 静态（Gattinoni 标准式）：
        MP = 0.098 × RR × Vt(L) × [Ppeak − 0.5 × (Pplat − PEEP)]
    - 动态（Pplat 不可信时，URS 备选简化式）：
        MP = 0.098 × RR × Vt(L) × 0.5 × (Ppeak + PEEP)
      注：动态式等价于把 ΔP 换成 (Ppeak − PEEP) 代入标准式，此处显式实现以保证可溯源。
    """
    if dp is None or dp_source is None:
        dp, dp_source = calc_dp(row)

    if dp_source == "none" or math.isnan(dp):
        return NAN, "none"

    vt = resolve_vt(row)
    rr = resolve_rr(row)
    pip = _num(row, "PIP")
    peep = _num(row, "PEEP")
    if math.isnan(vt) or math.isnan(rr) or math.isnan(pip):
        return NAN, "none"

    vt_l = vt / 1000.0
    if dp_source == "static":
        return 0.098 * rr * vt_l * (pip - 0.5 * dp), "static"

    if math.isnan(peep):
        return NAN, "none"
    return 0.098 * rr * vt_l * 0.5 * (pip + peep), "dynamic"


def calculate_dp(row: dict) -> float:
    """兼容旧签名：只返回 ΔP 数值。"""
    return calc_dp(row)[0]


def calculate_mp(row: dict, dp: Optional[float] = None) -> float:
    """兼容旧签名：只返回 MP 数值。"""
    return calc_mp(row, dp)[0]


def enrich_rows(rows: list) -> list:
    """给已采集的行列表补齐计算字段（原地修改并返回）。

    新增字段：
        VT        解析后的潮气量 mL
        RR        解析后的呼吸频率 bpm
        dP        ΔP
        MP        机械功率 J/min
        dp_source 'static' | 'dynamic' | 'none'
        mp_source 同上
        CRS       单次顺应性 Vt/ΔP（mL/cmH2O）
    """
    for r in rows:
        r["VT"] = resolve_vt(r)
        r["RR"] = resolve_rr(r)
        dp, dsrc = calc_dp(r)
        r["dP"] = dp
        r["dp_source"] = dsrc
        mp, msrc = calc_mp(r, dp, dsrc)
        r["MP"] = mp
        r["mp_source"] = msrc
        # 顺应性 CRS = Vt(mL) / ΔP(cmH2O)，单位 mL/cmH2O
        r["CRS"] = (r["VT"] / dp) if (not math.isnan(dp) and dp > 0
                                      and not math.isnan(r["VT"])) else NAN
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
    """区间 [v0,v1]（时长 dt_min 分钟）内 (值−thr) 的梯形 AUC，单位「值单位 × 分钟」。"""
    if math.isnan(v0) or math.isnan(v1):
        return 0.0
    e0, e1 = max(0.0, v0 - thr), max(0.0, v1 - thr)
    return (e0 + e1) / 2.0 * dt_min


# ════════════════════ 时间轴累积引擎（URS FR-01 / FR-04）════════════════════
#
# 关键修正（2026-09-09）：旧实现里有 `if dt_min <= 0 or dt_min > 1: continue`，
# 会把真实稀疏数据（实测平均 37 min 一批）的**所有**区间丢弃，导致
# TAT / AUC / 累积能量恒为 0、有效通气时长恒为 0.07 min。现按 URS 口径重写：
#   · 按「真实时间戳」积分，而非按采样点数假设 4 s
#   · 缺失 ≤ 4 h  → 前向填充，全额计入有效通气与暴露
#   · 缺失 > 4 h  → 只填充 4 h，超出部分标记「数据断流」，暂停积分并单独累计
#   · PTA = TAT / 有效通气总时长

def integrate_series(points: list, thresholds: dict,
                     ff_limit_min: float = MAX_FORWARD_FILL_MIN) -> dict:
    """对一条时间序列做时间加权累积。

    参数
        points      : [(ts_ms, value|NaN), ...] 必须按时间升序
        thresholds  : {名称: 阈值}，例如 {"dp": 15.0, "mp17": 17.0}
        ff_limit_min: 前向填充上限（分钟），默认 4 h

    返回
        {
          "tat_min":  {名称: 超阈分钟数},
          "auc_min":  {名称: 超阈 AUC（值单位 × 分钟）},
          "vent_min": 有效通气总时长（分钟，含 ≤4h 前向填充）,
          "gap_min":  断流时长（分钟，只统计超出 ff_limit 的部分）,
          "n_gaps":   >4h 断流段数,
          "span_min": 首尾时间跨度（分钟）,
          "energy_min": Σ mean(value) × Δt（仅对第一个阈值之外的通用积分，见下）
        }
    """
    names = list(thresholds.keys())
    tat = {n: 0.0 for n in names}
    auc = {n: 0.0 for n in names}
    vent_min = gap_min = 0.0
    n_gaps = 0

    if len(points) < 2:
        return {"tat_min": tat, "auc_min": auc, "vent_min": 0.0, "gap_min": 0.0,
                "n_gaps": 0, "span_min": 0.0}

    span_min = (points[-1][0] - points[0][0]) / 60000.0

    for i in range(1, len(points)):
        t0, v0 = points[i - 1]
        t1, v1 = points[i]
        if math.isnan(v0) or math.isnan(v1):
            continue                       # 任一端无有效读数 → 该区间不积分
        dt = (t1 - t0) / 60000.0
        if dt <= 0:
            continue

        # ── 断流判定 ──
        if dt > ff_limit_min:
            dt_eff = ff_limit_min          # 只前向填充 4 h
            gap_min += dt - ff_limit_min
            n_gaps += 1
        else:
            dt_eff = dt
        vent_min += dt_eff

        for n in names:
            thr = thresholds[n]
            tat[n] += _above_minutes(v0, v1, dt_eff, thr)
            auc[n] += _auc_above(v0, v1, dt_eff, thr)

    return {"tat_min": tat, "auc_min": auc, "vent_min": vent_min,
            "gap_min": gap_min, "n_gaps": n_gaps, "span_min": span_min}


def build_points(rows: list, key: str) -> list:
    """从已 enrich 的行里抽出 (ts, 值) 序列，按时间升序。"""
    pts = []
    for r in rows:
        ts = r.get("ts")
        v = r.get(key, NAN)
        if ts is None:
            continue
        pts.append((int(ts), NAN if v is None else float(v)))
    pts.sort(key=lambda x: x[0])
    return pts


def compute_cumulative(rows: list, ff_limit_min: float = MAX_FORWARD_FILL_MIN) -> dict:
    """对一个窗口内的行做 ΔP / MP 双轨累积（URS FR-04）。

    返回 TAT(h)、AUC(值单位·h)、PTA(%)、有效通气时长、断流、DCR 等。
    """
    dp_pts = build_points(rows, "dP")
    mp_pts = build_points(rows, "MP")

    dp_res = integrate_series(
        dp_pts, {"dp": DP_THRESHOLD}, ff_limit_min)
    mp_res = integrate_series(
        mp_pts,
        {"mp17": MP_THRESHOLD,
         "mp18": MP_HIGH_STRATUM_THRESHOLD,
         "mp20": MP_LOW_STRATUM_THRESHOLD},
        ff_limit_min,
    )

    vent_min = max(dp_res["vent_min"], mp_res["vent_min"])
    gap_min = max(dp_res["gap_min"], mp_res["gap_min"])
    n_gaps = max(dp_res["n_gaps"], mp_res["n_gaps"])

    # 机械能 = Σ MP × Δt（J/min × min = J），与 MP 同步积分
    energy_j = 0.0
    for i in range(1, len(mp_pts)):
        t0, v0 = mp_pts[i - 1]
        t1, v1 = mp_pts[i]
        if math.isnan(v0) or math.isnan(v1):
            continue
        dt = (t1 - t0) / 60000.0
        if dt <= 0:
            continue
        dt_eff = min(dt, ff_limit_min)
        energy_j += (v0 + v1) / 2.0 * dt_eff

    def _h(minutes):
        return minutes / 60.0

    dp_tat_h = _h(dp_res["tat_min"]["dp"])
    mp_tat_h = {k: _h(v) for k, v in mp_res["tat_min"].items()}
    dp_auc_h = _h(dp_res["auc_min"]["dp"])
    mp_auc_h = {k: _h(v) for k, v in mp_res["auc_min"].items()}

    # 数据完整率 DCR（两个口径）
    #   dcr     = 有效通气时长（含 ≤4h 前向填充）/ 跨度 —— 用于置信度降级提示（URS FR-01）
    #   dcr_raw = 实际有上报的分钟数 / 跨度分钟数 —— 原始上报密度（不含填充，反映真实稀疏度）
    span = max(dp_res["span_min"], mp_res["span_min"])
    dcr = (vent_min / span * 100.0) if span > 0 else 0.0
    reported_minutes = len({int(ts) // 60000 for ts, v in (dp_pts + mp_pts)
                            if not math.isnan(v)})
    dcr_raw = (reported_minutes / span * 100.0) if span > 0 else 0.0

    return {
        "vent_minutes": vent_min,
        "vent_hours": _h(vent_min),
        "gap_minutes": gap_min,
        "n_gaps": n_gaps,
        "span_minutes": span,
        "dp_tat_hours": dp_tat_h,
        "mp_tat_hours": mp_tat_h,               # {"mp17":..,"mp18":..,"mp20":..}
        "dp_auc_h": dp_auc_h,                   # cmH2O·h
        "mp_auc_h": mp_auc_h,                   # {"mp17":..} J·h/min
        "dp_pta": (dp_tat_h / _h(vent_min) * 100.0) if vent_min > 0 else 0.0,
        "mp_pta": (mp_tat_h["mp17"] / _h(vent_min) * 100.0) if vent_min > 0 else 0.0,
        "energy_j": energy_j,
        "dcr": dcr,
        "dcr_raw": dcr_raw,
        "dcr_low": dcr < DCR_LOW_THRESHOLD,
    }


def weighted_mean_series(points: list, ff_limit_min: float = MAX_FORWARD_FILL_MIN) -> float:
    """按「有效通气时长」加权的时间序列均值（与 integrate_series 同一断流规则）。

    用于顺应性 CRS 等需要时间加权的均值，保证「全程累计」与「24h 滚动窗口」
    两套口径得到一致的分层结论（此前全程 33.3 / 窗口 29.8 曾给出相反分层）。
    """
    num = den = 0.0
    for i in range(1, len(points)):
        t0, v0 = points[i - 1]
        t1, v1 = points[i]
        dt = (t1 - t0) / 60000.0
        if dt <= 0:
            continue
        dt_eff = min(dt, ff_limit_min)
        vals = [v for v in (v0, v1) if not math.isnan(v)]
        if not vals:
            continue
        num += (sum(vals) / len(vals)) * dt_eff
        den += dt_eff
    return (num / den) if den > 0 else NAN


def boundary_increment(prev_ts, prev_dp, prev_mp, cur_ts, cur_dp, cur_mp,
                       ff_limit_min: float = MAX_FORWARD_FILL_MIN) -> dict:
    """计算「上一聚合点 → 当前聚合点」这一个区间的累积增量。

    聚合层逐分钟只拿到 1 个点（甚至 0 个），无法在分钟内部积分；
    真正的暴露发生在两个上报点之间，因此必须在跨越分钟边界处做积分。
    断流规则与 integrate_series 完全一致（≤4h 前向填充，>4h 记断流）。

    返回 {"dp_over_min","mp17","mp18","mp20","dp_auc","mp17_auc","mp18_auc","mp20_auc",
          "vent_min","gap_min","n_gaps","energy_j"}
    """
    zero = {
        "dp_over_min": 0.0, "mp17": 0.0, "mp18": 0.0, "mp20": 0.0,
        "dp_auc": 0.0, "mp17_auc": 0.0, "mp18_auc": 0.0, "mp20_auc": 0.0,
        "vent_min": 0.0, "gap_min": 0.0, "n_gaps": 0, "energy_j": 0.0,
    }
    if not prev_ts or not cur_ts or cur_ts <= prev_ts:
        return zero

    dt = (cur_ts - prev_ts) / 60000.0
    if dt <= 0:
        return zero

    dt_eff = min(dt, ff_limit_min)
    gap_min = max(0.0, dt - ff_limit_min)
    n_gaps = 1 if gap_min > 0 else 0

    out = dict(zero)
    out["vent_min"] = dt_eff
    out["gap_min"] = gap_min
    out["n_gaps"] = n_gaps

    if not (math.isnan(prev_dp) or math.isnan(cur_dp)):
        out["dp_over_min"] = _above_minutes(prev_dp, cur_dp, dt_eff, DP_THRESHOLD)
        out["dp_auc"] = _auc_above(prev_dp, cur_dp, dt_eff, DP_THRESHOLD)

    if not (math.isnan(prev_mp) or math.isnan(cur_mp)):
        for key, thr in (("mp17", MP_THRESHOLD),
                         ("mp18", MP_HIGH_STRATUM_THRESHOLD),
                         ("mp20", MP_LOW_STRATUM_THRESHOLD)):
            out[key] = _above_minutes(prev_mp, cur_mp, dt_eff, thr)
            out[key + "_auc"] = _auc_above(prev_mp, cur_mp, dt_eff, thr)
        out["energy_j"] = (prev_mp + cur_mp) / 2.0 * dt_eff

    return out


def compute_exposure(rows: list) -> dict:
    """
    计算暴露指标与累积暴露（双轨制：ΔP 轨道 + MP 轨道，按文献精读）。

    累积暴露度量：高暴露分钟数（→高暴露小时数）、超阈 AUC、总机械能、顺应性。
    参数 rows 为已 enrich 过的行（含 dP / MP / CRS 字段）。
    """
    dp_vals = [r["dP"] for r in rows if not math.isnan(r.get("dP", NAN))]
    mp_vals = [r["MP"] for r in rows if not math.isnan(r.get("MP", NAN))]

    dp_max = max(dp_vals) if dp_vals else NAN
    dp_mean = sum(dp_vals) / len(dp_vals) if dp_vals else NAN
    dp_over = [v for v in dp_vals if v > DP_THRESHOLD]
    dp_over_pct = (len(dp_over) / len(dp_vals) * 100) if dp_vals else 0.0

    mp_max = max(mp_vals) if mp_vals else NAN
    mp_mean = sum(mp_vals) / len(mp_vals) if mp_vals else NAN
    mp_over = [v for v in mp_vals if v > MP_THRESHOLD]
    mp_over_pct = (len(mp_over) / len(mp_vals) * 100) if mp_vals else 0.0

    # ── 顺应性 CRS = VT(mL) / ΔP ──
    # 修正：旧实现要求 len(rows)>=2 才进循环，导致单批次分钟恒为 NaN，
    #       分层永远落到「低顺应性」从而放宽了报警判据。现逐点统计。
    crs_vals = [r["CRS"] for r in rows
                if not math.isnan(r.get("CRS", NAN)) and r.get("CRS", NAN) > 0]
    compliance_mean = (sum(crs_vals) / len(crs_vals)) if crs_vals else NAN

    # ── 累积暴露（时间加权，含 ≤4h 前向填充 / >4h 断流）──
    cum = compute_cumulative(rows)

    # 风险评级（瞬时单值维度）
    risk = classify_risk(dp_max, dp_over_pct, mp_max, mp_over_pct)

    # 累积维度评级（高暴露小时数 + 顺应性分层）
    dp_over_hours = cum["dp_tat_hours"]
    stratum = "high" if (not math.isnan(compliance_mean)
                         and compliance_mean > COMPLIANCE_STRATUM_THRESHOLD) else "low"
    mp_over_hours = (cum["mp_tat_hours"]["mp18"] if stratum == "high"
                     else cum["mp_tat_hours"]["mp20"])
    cum_risk = classify_cumulative_risk(dp_over_hours, mp_over_hours, stratum)

    # 计算模式溯源：窗口内只要出现过动态降级，就标记（供前端打 *Dyn*）
    dp_sources = [r.get("dp_source") for r in rows]
    mp_sources = [r.get("mp_source") for r in rows]
    dp_source = ("dynamic" if "dynamic" in dp_sources
                 else ("static" if "static" in dp_sources else "none"))
    mp_source = ("dynamic" if "dynamic" in mp_sources
                 else ("static" if "static" in mp_sources else "none"))

    return {
        "dp": {
            "max": dp_max, "mean": dp_mean,
            "threshold": DP_THRESHOLD,
            "over_count": len(dp_over), "over_pct": dp_over_pct,
            "auc": cum["dp_auc_h"] * 60.0,      # 兼容旧字段：单位为 cmH2O·min
        },
        "mp": {
            "max": mp_max, "mean": mp_mean,
            "threshold": MP_THRESHOLD,
            "over_count": len(mp_over), "over_pct": mp_over_pct,
            "auc": cum["mp_auc_h"]["mp17"] * 60.0,
        },
        # 原始累积累加量（供聚合层滚动），单位：分钟 / 值单位·分钟
        "cum_over_minutes": {
            "dp": cum["dp_tat_hours"] * 60.0,
            "mp17": cum["mp_tat_hours"]["mp17"] * 60.0,
            "mp18": cum["mp_tat_hours"]["mp18"] * 60.0,
            "mp20": cum["mp_tat_hours"]["mp20"] * 60.0,
        },
        "cum_auc_above": {
            "dp": cum["dp_auc_h"] * 60.0,
            "mp17": cum["mp_auc_h"]["mp17"] * 60.0,
            "mp18": cum["mp_auc_h"]["mp18"] * 60.0,
            "mp20": cum["mp_auc_h"]["mp20"] * 60.0,
        },
        "cum_energy_j": cum["energy_j"],
        "vent_minutes": cum["vent_minutes"],
        "gap_minutes": cum["gap_minutes"],
        "n_gaps": cum["n_gaps"],
        "dcr": cum["dcr"],
        "dcr_low": cum["dcr_low"],
        "dp_source": dp_source,
        "mp_source": mp_source,
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


def classify_instant_risk(dp: float, mp: float) -> int:
    """
    瞬时单值风险评级 L1-L4（只看当前一次读数，不做"超阈占比"放大）。

    与 classify_risk 的关键区别：单值场景没有"占比"概念，不能用 over_pct=100
    直接判 L4（那会把一次轻微越阈误判为 L4 危险）。规则（合理默认，可随临床调整）：
      L1 正常：ΔP≤阈 且 MP≤阈
      L2 关注：任一项越阈（单次超阈）
      L3 警告：ΔP≥20 或 MP≥24（明显超阈）
      L4 危险：ΔP≥25 或 MP≥30（大幅超阈）
    """
    def _over(v, thr):
        return v is not None and not math.isnan(v) and v > thr

    risk = 1
    if _over(dp, DP_THRESHOLD) or _over(mp, MP_THRESHOLD):
        risk = 2
    if _over(dp, 25) or _over(mp, 30):
        risk = 4
    elif _over(dp, 20) or _over(mp, 24):
        risk = 3
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
