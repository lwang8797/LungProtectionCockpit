# -*- coding: utf-8 -*-
"""
gen_urs_testdata.py - URS 测试数据生成器

用途：URS 要求 P1 各算法（斜率 / CUSUM / G0-G3 防抖 / AUC / PTA）按「连续 1-min 通气序列」
原样验证。当前库内真实设备数据极稀疏（23 通气分钟 / 24h），无法触发这些引擎。
本生成器产出与 `measure_param` 库读结构**完全一致**的原始批次文档
（每时间戳一批、16 参数、value 为字符串），可：
  1) 直接写入临时 MongoDB 集合跑 collector→aggregator 全链路；或
  2) 转成 enrich_rows 之后的行列表喂 analyzer / calculator 纯函数。

产出的生理轨迹可配置，用于构造 CUSUM 变化点、持续越限、断流、顺应性分层等场景。

典型用法：
  python gen_urs_testdata.py --hours 72 --scenario step --out measure_param_dump.json
  python gen_urs_testdata.py --hours 24 --scenario step --series-only   # 打印 enrich 行
"""
from __future__ import annotations
import argparse, json, math, random
from datetime import datetime, timezone


# ── 默认基准呼吸力学（近似真实谊安呼吸机，Pplat 介于 PEEP 与 PIP 之间）──
BASE = {
    "PIP": 22.0,      # 峰压
    "Pplat": 18.0,    # 平台压
    "Pmean": 9.0,
    "PEEP": 5.0,
    "Vte": 420.0,     # mL
    "Vti": 415.0,     # mL
    "FiO2": 40.0,
    "MVe": 7.5,
    "ftotal": 18.0,
    "fspont": 0.0,
    "Cdyn": 32.0,
    "WOB": 0.5,
    "PR": 0.0,
    "DrivePress": 13.0,   # = Pplat - PEEP 的直读（静态）
}

# paramId -> (标准名, 单位)
PARAM_CATALOG = {
    101: ("PIP", "cmH2O"), 102: ("Pplat", "cmH2O"), 103: ("Pmean", "cmH2O"),
    104: ("PEEP", "cmH2O"), 106: ("Vte", "mL"), 107: ("FiO2", "%"),
    110: ("Vti", "mL"), 111: ("MVe", "L/min"), 113: ("ftotal", "br/min"),
    114: ("fspont", "br/min"), 116: ("Cdyn", "mL/cmH2O"), 118: ("WOB", "J/L"),
    128: ("PR", "br/min"), 160: ("DrivePress", "cmH2O"),
}


def make_param_docs(device_id: str, ts_ms: int, vals: dict) -> list:
    """把一个时间戳的一批参数值铺平成 measure_param 原始文档（value 为字符串）。"""
    docs = []
    for pid, (name, unit) in PARAM_CATALOG.items():
        v = vals.get(name, BASE.get(name))
        docs.append({
            "deviceId": device_id,
            "timeStamp": ts_ms,
            "paramId": pid,
            "name": name,
            "value": ("OFF" if v is None else format(v, ".1f")),
            "unitName": unit,
        })
    return docs


def physiology_at(minute_idx: int, scenario: str, seg: dict) -> dict:
    """返回第 minute_idx 分钟（0 起）的呼吸力学瞬时值。

    scenario:
      - stable : 恒定基准
      - ramp   : PEEP 恒定、Pplat/PIP 缓慢抬升（用于斜率/基线漂移）
      - step   : 前半段低，后一半抬到 Pplat 20（ΔP=15）持续 → 用于 CUSUM/防抖 G1
      - surge  : 偶发 5 分钟峰值（模拟咳嗽/吸痰伪差）叠加在 stable 上
      - gap    : 中段 >4h 断流（见 make_series 的 gap 参数），此处返回 None 跳过
      - highcomp/lowcomp : 高/低顺应性（Cdyn / Vt 对应调整）
    """
    b = dict(BASE)
    if scenario == "stable":
        return b
    if scenario == "ramp":
        # Pplat 从 16 线性到 22，PIP 同步 +4，ΔP 从 11 到 17，MP 递增 → 平滑斜率
        ramp = seg.get("ramp_per_hour", 0.6) * (minute_idx / 60.0)
        b["Pplat"] = BASE["Pplat"] + ramp
        b["PIP"] = BASE["PIP"] + ramp
        b["DrivePress"] = BASE["DrivePress"] + ramp
        b["Pmean"] = BASE["Pmean"] + ramp * 0.5
        return b
    if scenario == "step":
        mid = seg.get("step_after_min", 360)   # 默认 6h 后抬升
        up = seg.get("step_dp", 15.0)          # 目标 ΔP
        if minute_idx >= mid:
            b["Pplat"] = b["PEEP"] + up
            b["PIP"] = b["PEEP"] + up + 4.0    # PIP 高于 Pplat 4（通过校验）
            b["DrivePress"] = up
            b["Pmean"] = b["PEEP"] + up * 0.5
        return b
    if scenario == "surge":
        # 每 90 分钟附近来一段 5 分钟 ΔP 冲到 25（伪差），其余稳定
        p = minute_idx % 90
        if 40 <= p <= 44:
            b["Pplat"] = b["PEEP"] + 25.0
            b["PIP"] = b["PEEP"] + 25.0 + 4.0
            b["DrivePress"] = 25.0
        return b
    if scenario == "highcomp":
        b["Cdyn"] = 45.0
        b["Vti"] = 560.0; b["Vte"] = 565.0
        return b
    if scenario == "lowcomp":
        b["Cdyn"] = 22.0
        b["Vti"] = 300.0; b["Vte"] = 302.0
        return b
    return b


def make_series(device_id: str, start_iso: str, hours: float = 24.0,
                scenario: str = "stable", seg: dict | None = None,
                every_min: int = 1, gap_minutes: list | None = None,
                include_gap_break: bool = True):
    """产出连续分钟批次的 (ts_ms, vals) 迭代。

    gap_minutes: [(start_idx, length_min)] 制造断流（期间无批次，用于测 CUSUM 重置/断流）。
    """
    seg = seg or {}
    start_dt = datetime.fromisoformat(start_iso).astimezone(timezone.utc)
    start_ms = int(start_dt.timestamp() * 1000)
    n = int(hours * 60)
    out = []
    for i in range(0, n, every_min):
        if gap_minutes:
            skip = any(g0 <= i < g0 + gl for g0, gl in gap_minutes)
            if skip:
                continue
        vals = physiology_at(i, scenario, seg)
        if vals is None:
            continue
        out.append((start_ms + i * 60000, vals))
    return out


def to_raw_docs(device_id: str, series) -> list:
    """把 make_series 的 [(ts, vals)] 铺平成 measure_param 原始文档列表。"""
    docs = []
    for ts, vals in series:
        docs.extend(make_param_docs(device_id, ts, vals))
    return docs


def series_to_rows(series) -> list:
    """把 [(ts, vals)] 转成 enrich_rows 之前的行（含 ts / dt / 各参数字段）。"""
    rows = []
    for ts, vals in series:
        r = dict(vals)
        r["ts"] = ts
        r["dt"] = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
        rows.append(r)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", default="TESTVENT01")
    ap.add_argument("--start", default="2026-09-01T00:00:00+00:00")
    ap.add_argument("--hours", type=float, default=24.0)
    ap.add_argument("--scenario", default="stable",
                    choices=["stable", "ramp", "step", "surge", "highcomp", "lowcomp"])
    ap.add_argument("--every", type=int, default=1, help="每几分钟一批")
    ap.add_argument("--gap", default="", help="断流区间: '360,240' = idx360起240min")
    ap.add_argument("--step-after", type=int, default=360)
    ap.add_argument("--ramp", type=float, default=0.6)
    ap.add_argument("--out", default="", help="输出原始文档 JSON 文件")
    ap.add_argument("--rows", action="store_true", help="打印 enrich 行（不写库）")
    args = ap.parse_args()

    gaps = []
    if args.gap:
        for part in args.gap.split(","):
            if part.strip():
                g0, gl = part.split(":")
                gaps.append((int(g0), int(gl)))

    series = make_series(
        args.device, args.start, args.hours, args.scenario,
        {"step_after_min": args.step_after, "ramp_per_hour": args.ramp},
        every_min=args.every, gap_minutes=gaps or None,
    )
    print(f"生成 {len(series)} 个通气分钟批次（{args.hours}h/{args.every}min 间隔, "
          f"scenario={args.scenario}, 断流区间={gaps}）")

    if args.out:
        docs = to_raw_docs(args.device, series)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(docs, f)
        print(f"原始文档已写入 {args.out}（{len(docs)} 条）")
        return

    if args.rows:
        rows = series_to_rows(series)
        # 用 calculator 透视并打印部分
        sys.path.insert(0, ".")
        try:
            from lung_protection_cockpit.calculator import enrich_rows
            enrich_rows(rows)
            print(f"{'idx':>4} {'min':>5} {'PEEP':>5} {'Pplat':>6} {'PIP':>5} "
                  f"{'Vti':>5} {'ftotal':>6} {'dP':>5} {'MP':>6}")
            for i, r in enumerate(rows[:30]):
                if i % 5 == 0:
                    print(f"{i:>4} {i:>5} {r.get('PEEP',0):>5.1f} "
                          f"{r.get('Pplat',0):>6.1f} {r.get('PIP',0):>5.1f} "
                          f"{r.get('Vti',0):>5.0f} {r.get('ftotal',0):>6.0f} "
                          f"{r.get('dP',0):>5.1f} {r.get('MP',0):>6.2f}")
            if len(rows) > 30:
                print(f"  ... 共 {len(rows)} 行")
        except Exception as e:
            import traceback; traceback.print_exc()
        return

    # 默认：打印头几批的原始文档样例
    docs = to_raw_docs(args.device, series[:1])
    print("首分钟原始文档样例（measure_param 结构）：")
    print(json.dumps(docs[:3], ensure_ascii=False, indent=1))


if __name__ == "__main__":
    import sys
    main()
