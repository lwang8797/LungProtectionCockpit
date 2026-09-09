# -*- coding: utf-8 -*-
"""
analyzer.py - URS FR-05 / FR-06 时变趋势与分级报警引擎（严格按 URS 原文口径）

输入约定：本模块所有函数作用在 **连续 1-min 通气序列** 上（URS 假定的分钟级采样）。
  series : list[dict]，每个 dict 至少含
           "ts"      : 分钟起点时间戳（毫秒）
           "dp"      : 该分钟驱动压（None/NaN = 无效）
           "mp"      : 该分钟机械功率（None/NaN = 无效）
           "crs"     : 该分钟顺应性（分层用，可 None）

不依赖 MongoDB。喂真实/生成数据的入口在调用方（api / scripts）构造 series。

核心函数：
  linear_slope    最小二乘斜率 β（值/小时）
  sliding_slopes  1h/6h/24h 滑动时间窗斜率
  cusum_track     双侧 CUSUM 变化点（k 容差/h 控制限，>4h 断流重置）
  DebounceGradeEngine  有状态逐分钟 G0-G3 防抖等级机
  run_engine_on_series  便捷跑批

"持续" = 真·连续有效通气分钟游程；断流(>240min 无分钟)重置游程。
"""
from __future__ import annotations
import math
from typing import Optional

# ── URS FR-06 防抖阈值 ──
HARD_FLOOR_MIN = 15     # <15min 瞬时超标一律不响
DEBOUNCE_G1_MIN = 60
DEBOUNCE_G2_MIN = 30
DEBOUNCE_G3_MIN = 15
RELEASE_MIN = 10        # 回落稳定 ≥10min 自动降级

# ── URS FR-04 阈值 ──
DP_THR = 15.0
MP_THR = 17.0
DP_G3_THR = 20.0        # G3 ΔP ≥20
MP_G3_HIGH_THR = 18.0;  MP_G3_HIGH_MIN = 120   # 高顺应 ≥2h
MP_G3_LOW_THR = 20.0;   MP_G3_LOW_MIN = 720    # 低顺应 ≥12h

# 24h 滚动 TAT 判据（小时）
DP_TAT_G1, DP_TAT_G2, DP_TAT_G3 = 5.0, 15.0, 30.0
MP_TAT_G1, MP_TAT_G2, MP_TAT_G3 = 7.0, 17.0, 17.0

# CUSUM
CUSUM_K_DP, CUSUM_K_MP = 2.0, 2.0
CUSUM_H_SIGMA = 5.0

GAP_RESET_MIN = 240.0   # 断流判定（对齐 P0 MAX_FORWARD_FILL_MIN）

G0, G1, G2, G3 = 0, 1, 2, 3
GRADE_LABELS = {G0: "G0 安全", G1: "G1 提示", G2: "G2 警告", G3: "G3 危险"}
GRADE_COLOR = {G0: "green", G1: "yellow", G2: "orange", G3: "red"}


def _v(x, default=float("nan")):
    if x is None:
        return default
    try:
        f = float(x)
    except (TypeError, ValueError):
        return default
    return f if not math.isnan(f) else default


def _valid_series_points(series, key):
    pts = []
    for r in series:
        ts = _v(r.get("ts"))
        val = _v(r.get(key))
        if not (math.isnan(ts) or math.isnan(val)):
            pts.append((ts, val))
    pts.sort(key=lambda p: p[0])
    return pts


# ────────────────────────────────────────────────────────────
#  线性回归斜率（FR-05.1）
# ────────────────────────────────────────────────────────────
def linear_slope(xs, ys):
    """最小二乘直线。返回 (beta_per_hour, intercept, r2, n)。点数<2 → (None,None,None,n)。"""
    n = len(xs)
    if n < 2:
        return None, None, None, n
    t0 = xs[0]
    xh = [(x - t0) / 3600000.0 for x in xs]
    mx = sum(xh) / n;  my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xh)
    if sxx <= 0:
        return None, None, None, n
    beta = sum((x - mx) * (y - my) for x, y in zip(xh, ys)) / sxx
    intercept = my - beta * mx
    r2 = 1.0
    if n > 2:
        sst = sum((y - my) ** 2 for y in ys)
        ssr = sum((beta * x + intercept - my) ** 2 for x in xh)
        r2 = (ssr / sst) if sst > 0 else 0.0
    return beta, intercept, r2, n


def sliding_slopes(series, key="dp", windows_min=(60, 360, 1440),
                   n_min=3, gap_reset_min=GAP_RESET_MIN):
    """1h/6h/24h 滑动时间窗斜率。断流处切段，只统计与最新点连续的段。

    返回 {window_h: {"beta","n","r2","insufficient","end_ts"}}
    窗内点数<n_min → beta=None, insufficient=True。
    """
    pts = _valid_series_points(series, key)
    if not pts:
        return {}
    latest = pts[-1][0]
    seg = [pts[-1]]
    for i in range(len(pts) - 2, -1, -1):
        if (seg[0][0] - pts[i][0]) > gap_reset_min * 60000:
            break
        seg.insert(0, pts[i])
    out = {}
    for wmin in windows_min:
        wh = wmin / 60.0
        win = [p for p in seg if (latest - p[0]) <= wmin * 60000]
        beta, _, r2, n = linear_slope([p[0] for p in win], [p[1] for p in win])
        insufficient = n < n_min
        out[wh] = {
            "beta": (None if insufficient else (round(beta, 4) if beta is not None else None)),
            "r2": (None if (insufficient or r2 is None) else round(r2, 3)),
            "n": n, "insufficient": insufficient, "end_ts": int(latest),
        }
    return out


# ────────────────────────────────────────────────────────────
#  CUSUM 变化点（FR-05.2）
# ────────────────────────────────────────────────────────────
def cusum_track(series, key="dp", k=None, h=None, mu0=None,
                mu0_span_min=1440, gap_reset_min=GAP_RESET_MIN,
                sigma_span_min=720, cooldown_min=60):
    """双侧 CUSUM。S± 超 h 记变化点并归零。>gap 断流重置。

    cooldown_min：同一侧变化点冷却时间（默认 60min）。CUSUM 对持续大漂移会在
    每个复位后很快再超限，冷却用于把「同一段持续漂移」折叠成一次变化点预警，
    避免刷屏（呈现层面只关心漂移的「起跳」时刻）。
    """
    pts = _valid_series_points(series, key)
    if len(pts) < 2:
        return {"change_points": [], "mu0": None, "k": k, "h": h,
                "Spos": 0.0, "Sneg": 0.0, "last_ts": None}
    if k is None:
        k = CUSUM_K_DP if key == "dp" else CUSUM_K_MP
    latest = pts[-1][0]

    base = []
    for p in reversed(pts):
        if (latest - p[0]) > mu0_span_min * 60000:
            break
        base.append(p[1])
    base.reverse()
    if mu0 is None:
        mu0 = (sum(base) / len(base)) if base else pts[-1][1]

    sig = []
    for p in reversed(pts):
        if (latest - p[0]) > sigma_span_min * 60000:
            break
        sig.append(p[1])
    sig.reverse()
    sigma = (sum((x - sum(sig) / len(sig)) ** 2 for x in sig) / (len(sig) - 1)) ** 0.5 \
        if len(sig) >= 2 else 1.0
    if sigma <= 0:
        sigma = 1.0
    if h is None:
        h = CUSUM_H_SIGMA * sigma

    Sp, Sn = 0.0, 0.0
    cps, prev_ts = [], None
    last_up_ts = last_dn_ts = None
    for ts, val in pts:
        if prev_ts is not None and (ts - prev_ts) > gap_reset_min * 60000:
            Sp = Sn = 0.0
            last_up_ts = last_dn_ts = None
        Sp = max(0.0, Sp + (val - mu0 - k))
        Sn = max(0.0, Sn + (mu0 - k - val))
        if Sp > h and (last_up_ts is None
                       or (ts - last_up_ts) > cooldown_min * 60000):
            cps.append({"ts": int(ts), "side": "up", "val": round(val, 2),
                        "S": round(Sp, 2)})
            Sp = 0.0
            last_up_ts = ts
        elif Sp > h:
            Sp = 0.0   # 冷却期内的再次超限：不重复报，仅复位
        if Sn > h and (last_dn_ts is None
                       or (ts - last_dn_ts) > cooldown_min * 60000):
            cps.append({"ts": int(ts), "side": "down", "val": round(val, 2),
                        "S": round(Sn, 2)})
            Sn = 0.0
            last_dn_ts = ts
        elif Sn > h:
            Sn = 0.0
        prev_ts = ts
    return {"change_points": cps, "mu0": round(mu0, 3), "k": k, "h": round(h, 3),
            "Spos": round(Sp, 3), "Sneg": round(Sn, 3), "last_ts": int(pts[-1][0])}


# ────────────────────────────────────────────────────────────
#  URS FR-06 G0-G3 矩阵 + 防抖引擎
# ────────────────────────────────────────────────────────────
def _tat_grade(dp_tat_24h, mp_tat_24h, stratum):
    """仅由 24h 滚动 TAT 决定的等级（累积暴露维度，天然抗瞬时，不需防抖、不会快速回落）。

    规则（FR-06 矩阵，取最高）：
      ΔP : <5→G0 ; ≥5→G1 ; ≥15→G2 ; ≥30→G3
      MP : <7→G0 ; ≥7→G1 ; ≥17→G2（矩阵中 MP 无单独 ≥30 的 G3 TAT，G3 由分层持续驱动）
    """
    g = G0
    if dp_tat_24h >= DP_TAT_G3:
        g = max(g, G3)
    elif dp_tat_24h >= DP_TAT_G2:
        g = max(g, G2)
    elif dp_tat_24h >= DP_TAT_G1:
        g = max(g, G1)
    if mp_tat_24h >= MP_TAT_G2:
        g = max(g, G2)
    elif mp_tat_24h >= MP_TAT_G1:
        g = max(g, G1)
    return g


def _sustain_grade(dp, mp, stratum, dp_sustain, dp_g3_sustain, dp_slope_up,
                   mp_sustain, mp_g3_sustain):
    """仅由「当前瞬时值 + 持续游程 + 斜率」决定的等级（瞬时维度，需防抖确认）。

    满足 URS 持续时长门槛才升对应级；<15min 一律压到 ≤G1（硬地板）。
    注意 ΔP 的 G1/G2 用「≥15 游程」，ΔP G3 需「≥20 专用游程」≥15min。
    """
    g = G0
    if dp is not None and dp >= DP_G3_THR and dp_g3_sustain >= DEBOUNCE_G3_MIN:
        g = max(g, G3)
    elif dp_sustain >= DEBOUNCE_G2_MIN and dp_slope_up:
        g = max(g, G2)
    elif dp_sustain >= DEBOUNCE_G1_MIN:
        g = max(g, G1)
    # MP（依顺应性分层 G3）
    g3_thr = MP_G3_HIGH_THR if stratum == "high" else MP_G3_LOW_THR
    g3_min = MP_G3_HIGH_MIN if stratum == "high" else MP_G3_LOW_MIN
    if mp is not None and mp >= g3_thr and mp_g3_sustain >= g3_min:
        g = max(g, G3)
    elif mp_sustain >= DEBOUNCE_G2_MIN:
        g = max(g, G2)   # MP≥17 持续 ≥30min（趋势上行/超阈持续 → 警告）
    elif mp_sustain >= DEBOUNCE_G1_MIN:
        g = max(g, G1)
    # 硬地板：任一值超标但持续 <15min 不允许进入 G2+（杜绝瞬时声光报警）
    if dp is not None and dp >= DP_THR and dp_sustain < HARD_FLOOR_MIN:
        g = min(g, G1)
    if mp is not None and mp >= MP_THR and mp_sustain < HARD_FLOOR_MIN:
        g = min(g, G1)
    return g


class DebounceGradeEngine:
    """有状态逐分钟 G0-G3 防抖机（真·连续分钟口径）。

    等级 = max(TAT 维度级, 瞬时持续维度级)。
    TAT 维度随 24h 滚动窗口自然升降（不可被 10min 回落快速解除）；
    瞬时持续维度需防抖确认（≥阈值持续 N min 升级），值回落并维持 ≥10min 后解除，
    解除仅作用于瞬时维度——不会把仍由高 TAT 撑住的等级错误压回 G1。
    """

    def __init__(self, stratum="high", gap_reset_min=GAP_RESET_MIN):
        self.stratum = stratum
        self.gap_reset_min = gap_reset_min
        self.grade = G0
        self.prev_grade = G0
        self.tat_grade = G0
        self.sustain_grade = G0
        self.dp_sustain = 0
        self.dp_g3_sustain = 0
        self.mp_sustain = 0
        self.mp_g3_sustain = 0
        self.below_dp = 0
        self.below_mp = 0
        self.prev_ts = None
        self.event = None
        self.history = []

    def reset(self):
        self.grade = G0;  self.prev_grade = G0
        self.tat_grade = G0;  self.sustain_grade = G0
        self.dp_sustain = 0;  self.dp_g3_sustain = 0
        self.mp_sustain = 0;  self.mp_g3_sustain = 0
        self.below_dp = 0;    self.below_mp = 0
        self.prev_ts = None;  self.event = None

    def _clear_runs(self):
        self.dp_sustain = 0;  self.dp_g3_sustain = 0
        self.mp_sustain = 0;  self.mp_g3_sustain = 0
        self.below_dp = 0;    self.below_mp = 0

    def feed(self, ts, dp=None, mp=None,
             dp_tat_24h=0.0, mp_tat_24h=0.0, dp_slope_up=False):
        """喂一个通气分钟。返回 (grade, event)。断流(>240min)重置游程。"""
        dp = None if dp is None or (isinstance(dp, float) and math.isnan(dp)) else float(dp)
        mp = None if mp is None or (isinstance(mp, float) and math.isnan(mp)) else float(mp)
        if self.prev_ts is not None:
            gap_min = (ts - self.prev_ts) / 60000.0
            if gap_min > self.gap_reset_min:
                self._clear_runs()
            elif gap_min > 1.5:
                self._clear_runs()   # ≤4h 缺失：不累计游程（真连续数据不走这）
        self.prev_ts = ts

        # ΔP 游程（≥15 通用；≥20 供 G3 专用）
        if dp is not None and dp >= DP_THR:
            self.dp_sustain += 1;  self.below_dp = 0
        else:
            self.dp_sustain = 0
            self.below_dp = (self.below_dp + 1) if dp is not None else 0
        self.dp_g3_sustain = (self.dp_g3_sustain + 1) \
            if (dp is not None and dp >= DP_G3_THR) else 0
        # MP 通用游程（≥17）
        if mp is not None and mp >= MP_THR:
            self.mp_sustain += 1;  self.below_mp = 0
        else:
            self.mp_sustain = 0
            self.below_mp = (self.below_mp + 1) if mp is not None else 0
        # MP G3 专用游程
        g3_thr = MP_G3_HIGH_THR if self.stratum == "high" else MP_G3_LOW_THR
        self.mp_g3_sustain = (self.mp_g3_sustain + 1) \
            if (mp is not None and mp >= g3_thr) else 0

        self.tat_grade = _tat_grade(dp_tat_24h, mp_tat_24h, self.stratum)
        # 回落解除：瞬时维度此前 ≥G2 且值已回落维持 ≥10min → 临时压低瞬时级
        sustain_suppressed = False
        if self.sustain_grade >= G2:
            if ((dp is not None and dp < DP_THR and self.below_dp >= RELEASE_MIN) or
                    (mp is not None and mp < MP_THR and self.below_mp >= RELEASE_MIN)):
                sustain_suppressed = True
        self.sustain_grade = _sustain_grade(
            dp, mp, self.stratum, self.dp_sustain, self.dp_g3_sustain, dp_slope_up,
            self.mp_sustain, self.mp_g3_sustain)
        if sustain_suppressed:
            self.sustain_grade = min(self.sustain_grade, G1)

        self.prev_grade = self.grade
        self.grade = max(self.tat_grade, self.sustain_grade)
        self.event = ("raise" if self.grade > self.prev_grade
                      else "release" if self.grade < self.prev_grade else None)
        self.history.append({"ts": ts, "grade": self.grade, "event": self.event,
                             "dp_sustain": self.dp_sustain,
                             "mp_sustain": self.mp_sustain,
                             "dp": (round(dp, 2) if dp is not None else None),
                             "mp": (round(mp, 2) if mp is not None else None)})
        return self.grade, self.event


def run_engine_on_series(series, stratum="high", dp_tat_24h=0.0, mp_tat_24h=0.0,
                         dp_slope_up=False):
    eng = DebounceGradeEngine(stratum=stratum)
    events = []
    for r in series:
        g, ev = eng.feed(_v(r.get("ts")), r.get("dp"), r.get("mp"),
                         dp_tat_24h, mp_tat_24h, dp_slope_up)
        if ev:
            events.append({"ts": _v(r.get("ts")), "event": ev, "grade": g})
    return eng, events
