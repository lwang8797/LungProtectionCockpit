# -*- coding: utf-8 -*-
"""
demo_p1.py - P1 端到端演示（生成数据驱动 analyzer 全链路，结果清晰可读）

分两段演示：
  段A 分级/防抖 (DebounceGradeEngine)：0-24h 稳定低 → 24-32h ΔP≈16 持续（防抖 60min 升 G1，
      若 6h 斜率向上升 G2）→ 32-33h 一次 <15min 的 ΔP=25 伪差（硬地板不升）→ 33-37h 回落
      稳定（10min 后自动解除）。结尾为低暴露（末 24h TAT 低 → 无累积撑高）。
  段B CUSUM：长稳定低基线 → 平移抬升，观察 up 变化点（μ0=过去24h稳态均值）。

跑： outputs/.venv/Scripts/python.exe scripts/demo_p1.py
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from datetime import datetime, timezone

import lung_protection_cockpit.analyzer as A
from lung_protection_cockpit.api import _compute_analysis

BASE = int(datetime(2026, 9, 10, tzinfo=timezone.utc).timestamp() * 1000)
MIN = 60000
def seg(start_min, dur_min, dp, mp, crs=45.0):
    return [{"ts": BASE + (start_min + i) * MIN, "dp": dp, "mp": mp, "crs": crs}
            for i in range(dur_min)]
def seg_b(base_ms, start_min, dur_min, dp, mp, crs=45.0):
    return [{"ts": base_ms + (start_min + i) * MIN, "dp": dp, "mp": mp, "crs": crs}
            for i in range(dur_min)]

ok = True
def ck(name, cond, detail=""):
    global ok
    print(("  PASS  " if cond else "  FAIL  ") + name + ("   " + detail if detail else ""))
    ok = ok and cond

# ────────── 段A：分级/防抖（rolling 24h 参数由引擎喂入）──────────
print("=" * 72)
print("段 A · G0-G3 分级与防抖（DebounceGradeEngine，逐分钟喂）")
print("=" * 72)
seriesA = []
seriesA += seg(0,    24*60, dp=8.0,  mp=8.0)           # 0-24h 稳定低 (G0)
seriesA += seg(24*60, 8*60,  dp=16.0, mp=14.0)         # 24-32h ΔP≥15 持续 8h → 防抖 60min 升 G1
# 32:00-32:10 一段 ΔP=25 伪差（<15min，硬地板应不许升 ≥G2），其余仍 16
for i in range(30):                                     # 32:00-32:30 保持 16
    seriesA.append({"ts": BASE + (32*60+i)*MIN, "dp": 16.0, "mp": 14.0})
for i in range(10):                                     # 32:30-32:40 伪差 ΔP=25
    seriesA.append({"ts": BASE + (32*60+30+i)*MIN, "dp": 25.0, "mp": 16.0})
seriesA += seg(33*60, 4*60,  dp=16.0, mp=14.0)         # 33-37h 回 16
seriesA += seg(37*60, 35*60, dp=8.0,  mp=8.0)          # 37-72h 回落稳定
# 防抖引擎是流式的，逐分钟喂；24h TAT 由调用方持续输入（演示给 0 观察纯瞬时持续行为）
engA = A.DebounceGradeEngine(stratum="high")
eventsA = []
for r in seriesA:
    g, ev = engA.feed(r["ts"], r["dp"], r["mp"], dp_tat_24h=0.0, mp_tat_24h=0.0)
    if ev:
        eventsA.append((r["ts"], ev, g, A.GRADE_LABELS[g]))
print(f"最终等级 G{engA.grade} {A.GRADE_LABELS[engA.grade]}   "
      f"ΔP≥15 持续 {engA.dp_sustain}min  ΔP≥20 持续 {engA.dp_g3_sustain}min")
print("事件时间线（@分钟，去重）:")
seen = set()
for ts, ev, g, lbl in eventsA:
    key = (ev, lbl)
    if key in seen:
        continue
    seen.add(key)
    hr = (ts - BASE) / MIN / 60
    print(f"    t={hr:6.1f}h  {ev:<7} → {lbl}")
raise_g1 = [(ts, g) for ts, ev, g, lbl in eventsA if ev == "raise" and lbl.startswith("G1")]
ck("ΔP≥15 持续≥60min 后升 G1（约 24h+60min 处）", len(raise_g1) >= 1)
if raise_g1:
    hr = (raise_g1[0][0] - BASE) / MIN / 60
    ck("G1 触发时刻 ≈ 25h（24h 起持续 60min）", 24.5 <= hr <= 26.0, f"at {hr:.1f}h")
# 32:30-32:40 的 ΔP=25 伪差仅 10min（≥20 持续 <15min）→ 不得升 ≥G2（硬地板）
# 检查伪差期间未进入 G2+：把伪差段单独喂给"全新"引擎看瞬时响应
eng_probe = A.DebounceGradeEngine(stratum="high")
probe_max = 0
for i in range(30):   # 预热 16
    eng_probe.feed(BASE + (32*60+i)*MIN, 16.0, 14.0)
for i in range(10):   # 伪差 25，连续只有 10min
    eng_probe.feed(BASE + (32*60+30+i)*MIN, 25.0, 16.0)
    probe_max = max(probe_max, eng_probe.grade)
ck("ΔP=25 伪差仅 10min 未升 ≥G2（硬地板）", probe_max <= A.G1, f"probe_max=G{probe_max}")
ck("结尾回落稳定后解除至 G0（纯瞬时维度无累积撑高）", engA.grade == A.G0)

# ────────── 段B：CUSUM 变化点 ──────────
print()
print("=" * 72)
print("段 B · CUSUM 变化点（低基线 24h → 抬升到 ΔP=16 持续 8h）")
print("=" * 72)
BASEb = int(datetime(2026, 9, 12, tzinfo=timezone.utc).timestamp() * 1000)
seriesB = []
seriesB += seg_b(BASEb, 0, 24*60, dp=8.0, mp=8.0)
seriesB += seg_b(BASEb, 24*60, 8*60, dp=16.0, mp=14.0)
ab = _compute_analysis("TESTVENT01", 32, series=seriesB)
c = ab["cusum"]["dp"]
ups = [x for x in c["change_points"] if x["side"] == "up"]
print(f"ΔP CUSUM: μ0={c['mu0']} k={c['k']} h={c['h']}  up变化点={len(ups)}")
for cp in ups[:4]:
    hr = (cp["ts"] - BASEb) / MIN / 60
    print(f"    t={hr:6.1f}h  side={cp['side']}  值={cp['val']}  S={cp['S']}")
ck("CUSUM 检出 up 变化点", len(ups) >= 1)
if ups:
    first_hr = (ups[0]["ts"] - BASEb) / MIN / 60
    ck("首个 up 点发生在抬升段(≥24h)", first_hr >= 24.0, f"at {first_hr:.1f}h")

print()
print("全部通过 ✅" if ok else "存在失败项 ❌")
sys.exit(0 if ok else 1)
