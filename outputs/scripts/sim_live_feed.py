# -*- coding: utf-8 -*-
"""
sim_live_feed.py - 模拟设备「实时上报」推送器

作用：按固定节奏向 measure_param 追加新的分钟批次，模拟呼吸机持续上报。
     配合 seed_sim_device.py 播种的历史数据 + 已启动的后端，前端就能看到：
       · 瞬时 ΔP / MP 数值随时间跳动
       · 24h 滚动窗口 TAT / AUC 累积
       · 双环剂量计填充比例增长
       · 触发越限预警、G0-G3 升降级（取决于场景）

用法:
    # 每 10 秒追加 1 个新分钟批次，沿用 step 场景（ΔP 持续 =15，越限）
    python scripts/sim_live_feed.py --device SIM900000001 --scenario step --interval 10

    # 每 5 秒推进 1 分钟，缓慢抬升（看斜率上行 + CUSUM  flagged）
    python scripts/sim_live_feed.py --scenario ramp --interval 5

    # 高频演示：每 2 秒推进 5 分钟（加快剂量累积，快速看到等级变化）
    python scripts/sim_live_feed.py --scenario surge --interval 2 --minutes-per-tick 5

参数说明:
    --interval          每次推送间隔（秒）
    --minutes-per-tick  每次推进多少「虚拟分钟」（默认 1）
    --max-ticks         最多推送多少次后自动停止（默认无限 Ctrl+C 退出）
    --start-idx         sceneario 的起始分钟序号（默认接续库内已有分钟数）

时间戳策略：从「库内该设备最新批次」或「当前时间」中取较大者继续推进，
这样数据始终在时间轴前端，前端刷新范围能看到它。
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gen_urs_testdata import physiology_at, make_param_docs  # noqa: E402

from lung_protection_cockpit.collector import get_db, ensure_indexes  # noqa: E402
from lung_protection_cockpit.config import COLL_RAW  # noqa: E402

DEFAULT_SIM_DEVICE = "SIM900000001"


def main():
    ap = argparse.ArgumentParser(description="模拟设备实时上报推送器")
    ap.add_argument("--device", default=DEFAULT_SIM_DEVICE)
    ap.add_argument("--scenario", default="step",
                    choices=["stable", "ramp", "step", "surge", "highcomp", "lowcomp"])
    ap.add_argument("--interval", type=float, default=10.0, help="推送间隔（秒）")
    ap.add_argument("--minutes-per-tick", type=int, default=1, help="每次推进的虚拟分钟数")
    ap.add_argument("--max-ticks", type=int, default=0, help="最多推送次数，0=无限")
    ap.add_argument("--start-idx", type=int, default=0, help="场景起始分钟序号")
    ap.add_argument("--step-after", type=int, default=360)
    ap.add_argument("--ramp", type=float, default=0.6)
    args = ap.parse_args()

    db = get_db()
    ensure_indexes(db)

    # 接续点：库内最新批次 vs 当前时间，取较大者
    latest = db[COLL_RAW].find_one({"deviceId": args.device}, sort=[("timeStamp", -1)])
    db_latest = int(latest["timeStamp"]) if latest else 0
    now_ms = int(time.time() * 1000) // 60000 * 60000      # 对齐到分钟
    cur_ms = max(db_latest, now_ms)
    idx = args.start_idx

    seg = {"step_after_min": args.step_after, "ramp_per_hour": args.ramp}
    fmt = lambda ms: datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%H:%M:%S UTC")

    print(f"推送器启动 | device={args.device} scenario={args.scenario} "
          f"间隔={args.interval}s 每次+{args.minutes_per_tick}min")
    print(f"  接续时间戳 = {fmt(cur_ms)}（库内最新 {fmt(db_latest) if db_latest else '无'}）")
    print("  Ctrl+C 停止\n")

    ticks = 0
    try:
        while args.max_ticks == 0 or ticks < args.max_ticks:
            cur_ms += args.minutes_per_tick * 60000
            idx += args.minutes_per_tick
            vals = physiology_at(idx, args.scenario, seg)
            docs = make_param_docs(args.device, cur_ms, vals)
            db[COLL_RAW].insert_many(docs, ordered=False)
            ticks += 1
            dp = float(vals["DrivePress"])
            print(f"  #{ticks} {fmt(cur_ms)}  ΔP={dp:5.1f}  "
                  f"Pplat={vals['Pplat']:.1f} PEEP={vals['PEEP']:.1f} "
                  f"Vti={vals['Vti']:.0f} RR={vals['ftotal']:.0f}")
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print(f"\n已停止，本次共推送 {ticks} 批")
        return 0
    print(f"\n推送完毕，共 {ticks} 批")
    return 0


if __name__ == "__main__":
    sys.exit(main())
