# -*- coding: utf-8 -*-
"""预警确认（ack）持久化回归测试

背景（bug 复现）：
    前端点击「确认」只改浏览器内存中的 alerts 数组，不请求后端；
    后端 cockpit_alerts 文档里也没有 active / acknowledged_by 字段，
    于是刷新页面重新拉取后，预警又回到「活动中/待确认」。

本测试用真实 HTTP 请求验证「确认状态被持久化」：
    ACK 一条预警 -> 重新 GET -> 该预警仍为 active=False 且带确认人。

用法:
    python outputs/scripts/test_alert_ack.py

说明:
    - 自动在 8091 端口拉起临时 uvicorn（不跑聚合守护进程，避免写入干扰）
    - 测试结束在 finally 中还原所有预警的原始确认状态，不污染生产数据
"""

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pymongo import MongoClient                      # noqa: E402
from lung_protection_cockpit.config import (        # noqa: E402
    MONGO_URI, MONGO_DB, COLL_ALERTS, DEVICE_ID,
)

PORT = int(os.environ.get("TEST_PORT", "8091"))
BASE = f"http://127.0.0.1:{PORT}"
PY = sys.executable

_passed, _failed = 0, 0


def check(cond, msg):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  [PASS] {msg}")
    else:
        _failed += 1
        print(f"  [FAIL] {msg}")
    return cond


def http(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def wait_server(timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(BASE + "/api/health", timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.5)
    return False


def main():
    db = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)[MONGO_DB]

    # ── 快照：记录所有预警的原始确认状态，测试后还原 ──
    snapshot = {
        str(d["_id"]): {
            "active": d.get("active"),
            "acknowledged": d.get("acknowledged"),
            "acknowledged_by": d.get("acknowledged_by"),
            "acknowledged_at": d.get("acknowledged_at"),
            "acknowledged_at_iso": d.get("acknowledged_at_iso"),
        }
        for d in db[COLL_ALERTS].find({"deviceId": DEVICE_ID})
    }
    print(f"快照 {len(snapshot)} 条预警的原始确认状态，测试结束会还原")

    proc = subprocess.Popen(
        [PY, "-m", "uvicorn", "lung_protection_cockpit.api:app",
         "--host", "127.0.0.1", "--port", str(PORT), "--log-level", "warning"],
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    try:
        if not wait_server():
            out = proc.stdout.read().decode(errors="ignore") if proc.stdout else ""
            print("服务启动失败:\n" + out)
            sys.exit(1)
        print(f"临时服务已启动: {BASE}\n")

        # ── 1. GET /api/alerts 结构校验 ──
        print("[1] GET /api/alerts 返回确认状态字段")
        st, d = http("GET", "/api/alerts?hours=168")
        check(st == 200, f"HTTP 200 (实际 {st})")
        alerts = d.get("alerts", [])
        check(len(alerts) > 0, f"返回 {len(alerts)} 条预警")
        check(all("id" in a for a in alerts), "每条预警都带 id（后端 _id 字符串）")
        check(all("active" in a for a in alerts), "每条预警都带 active 字段")
        check(all("acknowledged" in a for a in alerts), "每条预警都带 acknowledged 字段")
        check("active_count" in d, "响应含 active_count")

        # ── 先全部复位为「活动中」，构造确定的初始状态 ──
        db[COLL_ALERTS].update_many(
            {"deviceId": DEVICE_ID},
            {"$set": {"active": True, "acknowledged": False},
             "$unset": {"acknowledged_by": "", "acknowledged_at": "",
                        "acknowledged_at_iso": ""}},
        )
        st, d = http("GET", "/api/alerts?hours=168")
        alerts = d["alerts"]
        active0 = d["active_count"]
        check(active0 == len(alerts), f"复位后全部活动中（{active0}/{len(alerts)}）")

        # ── 2. 核心回归：确认单条 -> 重新拉取仍是已确认 ──
        print("\n[2] 确认单条预警后，重新拉取仍为已确认（本 bug 的核心断言）")
        target = alerts[0]
        tid = target["id"]
        st, d = http("POST", f"/api/alerts/{tid}/ack", {"operator": "测试医师"})
        check(st == 200, f"ACK 返回 200 (实际 {st})")
        check(d.get("alert", {}).get("acknowledged") is True, "响应 acknowledged=True")
        check(d.get("alert", {}).get("acknowledged_by") == "测试医师", "响应记录了确认人")

        st, d = http("GET", "/api/alerts?hours=168")
        again = next((a for a in d["alerts"] if a["id"] == tid), None)
        check(again is not None, "重新拉取仍能看到该预警")
        if again:
            check(again["active"] is False, "★ 刷新后 active 仍为 False（bug 已修复）")
            check(again["acknowledged"] is True, "★ 刷新后 acknowledged 仍为 True")
            check(again["acknowledged_by"] == "测试医师", "★ 刷新后确认人仍为「测试医师」")
            check(again.get("acknowledged_at") is not None, "记录了确认时间戳")
        check(d["active_count"] == active0 - 1, f"活动中计数 -1（{d['active_count']}）")

        # ── 3. 非法 / 不存在 ID ──
        print("\n[3] 异常入参处理")
        st, _ = http("POST", "/api/alerts/not-an-objectid/ack", {"operator": "x"})
        check(st == 400, f"非法 ID 返回 400 (实际 {st})")
        st, _ = http("POST", "/api/alerts/000000000000000000000000/ack", {"operator": "x"})
        check(st == 404, f"不存在 ID 返回 404 (实际 {st})")

        # ── 4. 批量确认 ──
        print("\n[4] 批量确认 ack-all")
        st, d = http("POST", "/api/alerts/ack-all?hours=168", {"operator": "值班护士"})
        check(st == 200, f"HTTP 200 (实际 {st})")
        check(d.get("acknowledged") == active0 - 1,
              f"确认了剩余 {active0 - 1} 条（实际 {d.get('acknowledged')}）")
        st, d = http("GET", "/api/alerts?hours=168")
        check(d["active_count"] == 0, f"★ 刷新后活动中共 0 条（实际 {d['active_count']}）")
        check(all(a["acknowledged"] for a in d["alerts"]), "★ 刷新后全部为已确认")

        # ── 5. JSON 可序列化（无 ObjectId / NaN 泄漏） ──
        print("\n[5] 响应可严格 JSON 序列化")
        st, d = http("GET", "/api/alerts?hours=168")
        try:
            json.dumps(d, allow_nan=False)
            check(True, "响应不含 NaN/Inf，且无裸 ObjectId")
        except ValueError as e:
            check(False, f"响应含非法浮点值: {e}")

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        # ── 还原原始确认状态 ──
        restored = 0
        for sid, fields in snapshot.items():
            from bson import ObjectId
            set_d, unset_d = {}, {}
            for k, v in fields.items():
                (unset_d if v is None else set_d)[k] = v
            op = {}
            if set_d:
                op["$set"] = set_d
            if unset_d:
                op["$unset"] = unset_d
            if op:
                db[COLL_ALERTS].update_one({"_id": ObjectId(sid)}, op)
            restored += 1
        print(f"\n已还原 {restored} 条预警的原始确认状态，临时服务已关闭")

    print(f"\n{'=' * 46}\n通过 {_passed} 项，失败 {_failed} 项\n{'=' * 46}")
    sys.exit(1 if _failed else 0)


if __name__ == "__main__":
    main()
