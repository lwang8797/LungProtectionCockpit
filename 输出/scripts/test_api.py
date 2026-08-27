# -*- coding: utf-8 -*-
"""
test_api.py - 肺保护驾驶舱后端 API 端到端测试脚本

用法:
  1. 先启动 API 服务:
     cd 输出
     PYTHONPATH=. python -m uvicorn lung_protection_cockpit.api:app --host 127.0.0.1 --port 9090
  2. 另开终端运行本脚本:
     python 输出/scripts/test_api.py
"""
import requests
import json
import sys
import time

BASE = "http://127.0.0.1:9090"
PASS = 0
FAIL = 0

def check(name, url, expected_keys=None, params=None):
    """测试一个端点"""
    global PASS, FAIL
    try:
        resp = requests.get(url, params=params, timeout=10)
        status = resp.status_code
        ok = status == 200
        data = resp.json() if ok else {}

        # 检查关键字段
        key_ok = True
        if expected_keys and ok:
            for k in expected_keys:
                if k not in data:
                    key_ok = False
                    break

        if ok and key_ok:
            PASS += 1
            print(f"  [PASS] {name}  ({status})")
            # 打印摘要
            summary_keys = expected_keys or list(data.keys())[:5]
            for k in summary_keys[:6]:
                v = data.get(k)
                if isinstance(v, (dict, list)):
                    v_str = json.dumps(v, ensure_ascii=False, default=str)[:120]
                else:
                    v_str = str(v)[:120]
                print(f"         {k}: {v_str}")
        else:
            FAIL += 1
            print(f"  [FAIL] {name}  (status={status})")
            if not key_ok:
                missing = [k for k in (expected_keys or []) if k not in data]
                print(f"         缺失字段: {missing}")
            print(f"         响应: {resp.text[:200]}")
    except Exception as e:
        FAIL += 1
        print(f"  [FAIL] {name}  异常: {e}")
    print()


def main():
    print("=" * 60)
    print("  肺保护驾驶舱 API 端到端测试")
    print("=" * 60)
    print()

    # 0. 健康检查
    print("[1/8] 健康检查")
    check("GET /api/health", f"{BASE}/api/health",
          expected_keys=["status", "device", "mongo", "data_range", "thresholds"])
    time.sleep(0.5)

    # 1. 总览仪表盘
    print("[2/8] 总览仪表盘")
    check("GET /api/overview", f"{BASE}/api/overview",
          expected_keys=["risk_level", "risk_label", "dp", "mp",
                         "risk_level_instant", "cumulative_risk_level", "cumulative"])
    time.sleep(0.5)

    # 2. DeltaP 趋势
    print("[3/8] DeltaP 趋势")
    check("GET /api/dp/trend", f"{BASE}/api/dp/trend",
          expected_keys=["device", "points", "series"], params={"points": 10})
    time.sleep(0.5)

    # 3. MP 趋势
    print("[4/8] MP 趋势")
    check("GET /api/mp/trend", f"{BASE}/api/mp/trend",
          expected_keys=["device", "points", "series"], params={"points": 10})
    time.sleep(0.5)

    # 4. 二维风险图
    print("[5/8] 二维风险图")
    check("GET /api/risk-map", f"{BASE}/api/risk-map",
          expected_keys=["device", "points", "thresholds", "series"], params={"points": 10})
    time.sleep(0.5)

    # 5. 预警列表
    print("[6/8] 预警列表")
    check("GET /api/alerts", f"{BASE}/api/alerts",
          expected_keys=["alerts", "count"], params={"hours": 168})
    time.sleep(0.5)

    # 6. 分钟级聚合明细
    print("[7/8] 分钟级聚合明细")
    check("GET /api/metrics/1min", f"{BASE}/api/metrics/1min",
          expected_keys=["device", "count", "minutes"], params={"limit": 3})
    time.sleep(0.5)

    # 7. 前端 HTML
    print("[8/9] 前端驾驶舱页面")
    try:
        resp = requests.get(f"{BASE}/", timeout=10)
        if resp.status_code == 200 and "<html" in resp.text.lower():
            global PASS
            PASS += 1
            print(f"  [PASS] GET /  ({resp.status_code}) — HTML {len(resp.text)} bytes")
        else:
            global FAIL
            FAIL += 1
            print(f"  [FAIL] GET /  (status={resp.status_code})")
    except Exception as e:
        FAIL += 1
        print(f"  [FAIL] GET /  异常: {e}")
    print()

    # 8. WebSocket
    print("[9/9] WebSocket 实时推送")
    try:
        import websocket
        ws = websocket.create_connection(f"ws://127.0.0.1:{BASE.split(':')[-1]}/ws", timeout=10)
        msg = ws.recv()
        import json as _json
        d = _json.loads(msg)
        if d.get("type") == "overview" and "data" in d:
            PASS += 1
            data = d["data"]
            print(f"  [PASS] WS /ws  连接成功")
            print(f"         初始推送: risk={data.get('risk_label')}, "
                  f"dp={data.get('dp',{}).get('current')}, "
                  f"mp={data.get('mp',{}).get('current'):.2f}")
        else:
            FAIL += 1
            print(f"  [FAIL] WS /ws  消息格式异常: {str(d)[:100]}")
        ws.close()
    except Exception as e:
        FAIL += 1
        print(f"  [FAIL] WS /ws  异常: {e}")
    print()

    # 汇总
    print("=" * 60)
    total = PASS + FAIL
    print(f"  测试结果: {PASS}/{total} 通过, {FAIL} 失败")
    if FAIL == 0:
        print("  *** 全部通过 ***")
    print("=" * 60)

    # Swagger 文档
    print()
    print("API 文档: http://127.0.0.1:9090/docs")
    print("总览数据: http://127.0.0.1:9090/api/overview")
    print("前端页面: http://127.0.0.1:9090/")

    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
