# -*- coding: utf-8 -*-
"""把本设备全部预警复位为「活动中 / 未确认」

仅用于测试复位。确认动作本身按审计要求是不可撤销的，
这里直接改库是测试环境的专用后门，请勿在生产中随意使用。

用法:
    python outputs/scripts/reset_alert_acks.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pymongo import MongoClient                      # noqa: E402
from lung_protection_cockpit.config import (        # noqa: E402
    MONGO_URI, MONGO_DB, COLL_ALERTS, DEVICE_ID,
)


def main():
    db = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)[MONGO_DB]
    res = db[COLL_ALERTS].update_many(
        {"deviceId": DEVICE_ID},
        {
            "$set": {"active": True, "acknowledged": False},
            "$unset": {"acknowledged_by": "", "acknowledged_at": "",
                       "acknowledged_at_iso": ""},
        },
    )
    total = db[COLL_ALERTS].count_documents({"deviceId": DEVICE_ID})
    print(f"已复位 {res.modified_count}/{total} 条预警为「活动中 / 未确认」（设备 {DEVICE_ID}）")


if __name__ == "__main__":
    main()
