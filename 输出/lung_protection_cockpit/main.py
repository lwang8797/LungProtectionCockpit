# -*- coding: utf-8 -*-
"""
main.py - 启动入口

用法:
  # 仅启动 API 服务
  python -m lung_protection_cockpit.main serve

  # 回填历史数据（默认 24h）
  python -m lung_protection_cockpit.main backfill --hours 24

  # 启动聚合守护进程
  python -m lung_protection_cockpit.main aggregate

  # 一键启动（回填 + API）
  python -m lung_protection_cockpit.main all --hours 2

环境变量:
  COCKPIT_HOST (default 0.0.0.0)
  COCKPIT_PORT (default 8080)
"""

import sys
import os
import logging
import argparse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("lung_cockpit")


def cmd_serve(args):
    """启动 FastAPI 服务"""
    import uvicorn
    from .api import app
    from .collector import get_db, ensure_indexes

    # 启动即确保索引（幂等），让 metrics_1min / measure_param 查询走索引
    try:
        ensure_indexes(get_db())
    except Exception as e:
        logger.warning(f"索引预创建失败（可忽略，查询会变慢）: {e}")

    host = os.environ.get("COCKPIT_HOST", "0.0.0.0")
    port = int(os.environ.get("COCKPIT_PORT", "8080"))

    logger.info(f"启动肺保护驾驶舱 API: http://{host}:{port}")
    logger.info(f"前端: http://{host}:{port}/")
    logger.info(f"API 文档: http://{host}:{port}/docs")

    uvicorn.run(app, host=host, port=port, log_level="info")


def cmd_backfill(args):
    """回填历史聚合数据"""
    from .collector import get_db, ensure_indexes
    from .aggregator import backfill

    db = get_db()
    ensure_indexes(db)
    logger.info(f"开始回填最近 {args.hours} 小时...")
    total = backfill(db, hours=args.hours)
    logger.info(f"回填完成: {total} 分钟已聚合")
    logger.info(f"可用 API 查看: GET /api/metrics/1min")


def cmd_aggregate(args):
    """启动聚合守护进程"""
    from .collector import get_db, ensure_indexes
    from .aggregator import run_continuous

    db = get_db()
    ensure_indexes(db)
    run_continuous(db, poll_interval=args.poll)


def cmd_all(args):
    """回填 + 启动 API"""
    from .collector import get_db, ensure_indexes
    from .aggregator import backfill

    db = get_db()
    ensure_indexes(db)
    logger.info(f"回填最近 {args.hours} 小时...")
    total = backfill(db, hours=args.hours)
    logger.info(f"回填完成: {total} 分钟已聚合")

    # 启动 API（cmd_serve 内也会再 ensure_indexes，幂等）
    cmd_serve(args)


def main():
    parser = argparse.ArgumentParser(
        description="肺保护驾驶舱 - 智能呼吸机 ΔP/MP 累积暴露监控服务",
    )
    sub = parser.add_subparsers(dest="command", help="子命令")

    # serve
    p_serve = sub.add_parser("serve", help="启动 API 服务")
    p_serve.set_defaults(func=cmd_serve)

    # backfill
    p_fill = sub.add_parser("backfill", help="回填历史聚合数据")
    p_fill.add_argument("--hours", type=float, default=24, help="回填小时数（默认24）")
    p_fill.set_defaults(func=cmd_backfill)

    # aggregate
    p_agg = sub.add_parser("aggregate", help="启动聚合守护进程")
    p_agg.add_argument("--poll", type=float, default=10.0, help="轮询间隔秒（默认10）")
    p_agg.set_defaults(func=cmd_aggregate)

    # all
    p_all = sub.add_parser("all", help="回填 + 启动 API")
    p_all.add_argument("--hours", type=float, default=24, help="回填小时数（默认24）")
    p_all.set_defaults(func=cmd_all)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
