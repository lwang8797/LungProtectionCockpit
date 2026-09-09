# -*- coding: utf-8 -*-
"""
rebuild_1min.py - 清空并按新口径重建 metrics_1min 聚合表

用途：计算口径（Vt→Vti、Pplat 可信性、时间加权积分 + 4h 前向填充 + 断流）
      变更后，历史聚合数据必须重算，否则旧文档里 cum_* 全是 0、CRS 是 NaN。

用法:
    python scripts/rebuild_1min.py            # 重建（默认 720 h）
    python scripts/rebuild_1min.py --hours 24
    python scripts/rebuild_1min.py --dry-run  # 只统计不写入
"""

import sys
import os
import argparse
import logging
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lung_protection_cockpit.collector import get_db, ensure_indexes, get_time_range
from lung_protection_cockpit.aggregator import backfill, _ensure_indexes
from lung_protection_cockpit.config import COLL_1MIN, DEVICE_ID


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=720.0, help="回填小时数（默认 720 = 30 天）")
    ap.add_argument("--device", type=str, default=DEVICE_ID)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s",
                        datefmt="%H:%M:%S")

    db = get_db()
    ensure_indexes(db)
    _ensure_indexes(db)

    before = db[COLL_1MIN].count_documents({"deviceId": args.device})
    print(f"重建前 metrics_1min 文档数: {before}")

    if args.dry_run:
        print("dry-run，未做改动")
        return

    if before:
        res = db[COLL_1MIN].delete_many({"deviceId": args.device})
        print(f"已清空 {res.deleted_count} 条旧聚合文档")

    _, latest_ts = get_time_range(db, args.device)
    if latest_ts == 0:
        print("无原始数据，跳过")
        return
    print(f"最新原始数据: {datetime.fromtimestamp(latest_ts/1000, tz=timezone.utc).isoformat()}")

    total = backfill(db, args.device, args.hours)
    after = db[COLL_1MIN].count_documents({"deviceId": args.device})
    print(f"重建完成：聚合 {total} 分钟，现有 {after} 条")


if __name__ == "__main__":
    main()
