# -*- coding: utf-8 -*-
"""
seed_sim_device.py - 把「模拟生成的连续分钟数据」播种到 MongoDB，用于全链路 / 前端演示

为什么需要它：
    真实设备数据极稀疏（每几十分钟一批），双环剂量计、累积暴露、24h 滚动窗口、
    滑动斜率 / CUSUM 变化点 / G0-G3 分级防抖这些 URS 功能在稀疏数据下几乎看不出效果。
    本脚本产出**结构与 measure_param 完全一致**的模拟批次文档（同一套 paramId 方案、
    value 为字符串、每时间戳 16 个参数），写入**独立的模拟设备 ID**，并重建 metrics_1min，
    之后用该设备启动后端即可在前端看到完整效果。

数据永远只写到模拟 deviceId（默认 SIM900000001），不会污染真实设备数据；可用 --clean 清除。

用法:
    # 1) 播种最近 48h「6h 后 ΔP 抬到 15 并持续越限」场景（推荐首次演示）
    python scripts/seed_sim_device.py --hours 48 --scenario step --step-after 360

    # 2) 缓慢基线漂移（看斜率 + CUSUM）
    python scripts/seed_sim_device.py --hours 48 --scenario ramp --ramp 0.8

    # 3) 伪差尖峰（看防抖：短时 ΔP=25 不应升级）
    python scripts/seed_sim_device.py --hours 24 --scenario surge

    # 4) 低顺应性分层（看 MP 安全上限切换 + 双环满量程变化）
    python scripts/seed_sim_device.py --hours 24 --scenario lowcomp

    # 5) 带 >4h 断流（idx 600 起断 300 分钟，看断流与 CUSUM 重置）
    python scripts/seed_sim_device.py --hours 48 --scenario step --gap 600:300

    # 6) 清除模拟设备的全部数据（原始批次 + 聚合 + 预警）
    python scripts/seed_sim_device.py --clean

播种后:
    启动后端指向该设备 (set COCKPIT_DEVICE_ID=SIM900000001)，浏览器打开页面即可。
    想看实时跳动，另开一个终端跑 scripts/sim_live_feed.py 持续推送新批次。
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gen_urs_testdata import make_series, to_raw_docs          # noqa: E402

from lung_protection_cockpit.collector import get_db, ensure_indexes  # noqa: E402
from lung_protection_cockpit.aggregator import backfill, _ensure_indexes  # noqa: E402
from lung_protection_cockpit.config import (                    # noqa: E402
    COLL_RAW, COLL_1MIN, COLL_ALERTS,
)

DEFAULT_SIM_DEVICE = "SIM900000001"
CHUNK = 5000


def parse_gaps(s: str):
    gaps = []
    if not s:
        return gaps
    for part in s.split(","):
        part = part.strip()
        if not part:
            continue
        g0, gl = part.split(":")
        gaps.append((int(g0), int(gl)))
    return gaps


def clean_device(db, device_id: str):
    """删除模拟设备的全部数据（原始批次 / 聚合 / 预警）。"""
    total = 0
    for coll in (COLL_RAW, COLL_1MIN, COLL_ALERTS):
        n = db[coll].count_documents({"deviceId": device_id})
        if n:
            db[coll].delete_many({"deviceId": device_id})
        print(f"  {coll}: 删除 {n} 条")
        total += n
    print(f"模拟设备 {device_id} 数据已清空（共 {total} 条）")


def seed(args):
    db = get_db()
    ensure_indexes(db)
    _ensure_indexes(db)

    # 结束时间 = 现在，开始时间 = now - hours —— 保证数据在时间轴上"新鲜"
    end_dt = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    hours = float(args.hours)
    start_dt = end_dt - timedelta(hours=hours)

    seg = {"step_after_min": args.step_after, "ramp_per_hour": args.ramp}
    gaps = parse_gaps(args.gap)

    series = make_series(
        args.device, start_dt.isoformat(), hours, args.scenario, seg,
        every_min=args.every, gap_minutes=gaps or None,
    )
    if not series:
        print("未生成任何批次，请检查参数（如 --hours 过小）")
        return 1

    docs = to_raw_docs(args.device, series)
    print(f"生成 {len(series)} 个通气分钟批次 / {len(docs)} 条原始文档 "
          f"（deviceId={args.device}, scenario={args.scenario}, "
          f"{hours}h, 间隔{args.every}min, 断流={gaps or '无'}）")

    # 清掉该设备旧数据再写入，保证重复播种幂等
    old = db[COLL_RAW].count_documents({"deviceId": args.device})
    if old:
        db[COLL_RAW].delete_many({"deviceId": args.device})
        print(f"  清掉该设备已有的 {old} 条原始文档")

    inserted = 0
    for i in range(0, len(docs), CHUNK):
        db[COLL_RAW].insert_many(docs[i:i + CHUNK], ordered=False)
        inserted += len(docs[i:i + CHUNK])
    print(f"  已写入 measure_param: {inserted} 条")

    if args.no_backfill:
        print("（--no-backfill：跳过 metrics_1min 重建）")
    else:
        t0 = time.time()
        oldof = db[COLL_1MIN].count_documents({"deviceId": args.device})
        if oldof:
            db[COLL_1MIN].delete_many({"deviceId": args.device})
        n = backfill(db, args.device, hours + 2)
        after = db[COLL_1MIN].count_documents({"deviceId": args.device})
        print(f"  metrics_1min 重建完成：{after} 分钟（backfill 返回 {n}），耗时 {time.time()-t0:.1f}s")

    first_ts, last_ts = series[0][0], series[-1][0]
    fmt = lambda ms: datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print("\n──── 播种完成 ────")
    print(f"  时间范围 : {fmt(first_ts)}  →  {fmt(last_ts)}")
    print(f"  下一步   : set COCKPIT_DEVICE_ID={args.device} && 启动后端，浏览器打开 http://localhost:8090/")
    print(f"  实时跳动 : python scripts/sim_live_feed.py --device {args.device} --scenario {args.scenario}")
    print(f"  清理     : python scripts/seed_sim_device.py --device {args.device} --clean")
    return 0


def main():
    ap = argparse.ArgumentParser(description="播种模拟设备数据用于 URS 全链路/前端演示")
    ap.add_argument("--device", default=DEFAULT_SIM_DEVICE)
    ap.add_argument("--hours", type=float, default=48.0)
    ap.add_argument("--scenario", default="step",
                    choices=["stable", "ramp", "step", "surge", "highcomp", "lowcomp"])
    ap.add_argument("--every", type=int, default=1, help="每几分钟一批（默认 1）")
    ap.add_argument("--gap", default="", help="断流区间，如 '600:300' = idx600 起断 300 分钟")
    ap.add_argument("--step-after", type=int, default=360, help="step 场景抬升起点（分钟）")
    ap.add_argument("--ramp", type=float, default=0.6, help="ramp 场景每小时 Pplat 增量")
    ap.add_argument("--clean", action="store_true", help="清除该模拟设备的全部数据")
    ap.add_argument("--no-backfill", action="store_true", help="只写原始数据，不重建聚合")
    args = ap.parse_args()

    db = get_db()
    if args.clean:
        clean_device(db, args.device)
        return 0
    sys.exit(seed(args))


if __name__ == "__main__":
    main()
