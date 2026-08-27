# -*- coding: utf-8 -*-
"""
肺保护驾驶舱 - 真实数据采集与 ΔP/MP 累积暴露计算脚本
连接测试环境 MongoDB (192.168.1.100:27017)，从 measure_param 采集真实参数，
计算 ΔP（驱动压）和 MP（机械功率），并输出累积暴露指标。

参数 paramId 映射:
  101 Ppeak(PIP)  102 Pplat   103 Pmean  104 PEEP   106 Vte
  107 FiO2        110 Vti     111 MVe    118 WOB    116 Cdyn
  128 PR(RR)      160 DrivePress(ΔP)
"""
import sys, math, json, csv
from datetime import datetime, timezone, timedelta
from pymongo import MongoClient

MONGO_URI = "mongodb://192.168.1.100:27017"
DB_NAME = "data-services-prod"
COLL = "measure_param"
DEVICE_ID = "1787816609"

# paramId -> 标准化名
# 注意: PR(128)是患者自主呼吸频率, 测试环境恒为"---"(无自主呼吸)
#       ftotal(113)是总呼吸频率(机控+自主), 用于 MP 公式中的 RR
PARAM_MAP = {
    101: "PIP", 102: "Pplat", 103: "Pmean", 104: "PEEP",
    106: "Vte", 107: "FiO2", 110: "Vti", 111: "MVe",
    113: "ftotal", 114: "fspont",
    116: "Cdyn", 118: "WOB", 128: "PR", 160: "DrivePress",
}

# 阈值
DP_THRESHOLD = 15.0   # cmH2O  (Amato 2015 NEJM)
MP_THRESHOLD = 17.0    # J/min  (Chest 2025)


def to_float(v):
    """安全转换 value 字段（字符串，可能有 'OFF'/'---'）"""
    try:
        return float(v)
    except (ValueError, TypeError):
        return float("nan")


def main():
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    db = client[DB_NAME]
    coll = db[COLL]

    # 1) 数据时间范围
    rng = coll.find({"deviceId": DEVICE_ID}, {"timeStamp": 1}).sort("timeStamp", -1).limit(1)
    latest = next(rng, None)
    if not latest:
        print("[ERROR] 未找到数据"); sys.exit(1)
    latest_ts = int(latest["timeStamp"])
    # 取最近 2 小时
    start_ts = latest_ts - 2 * 3600 * 1000
    latest_dt = datetime.fromtimestamp(latest_ts / 1000, tz=timezone.utc)
    start_dt = datetime.fromtimestamp(start_ts / 1000, tz=timezone.utc)
    print(f"[INFO] 设备 {DEVICE_ID}")
    print(f"[INFO] 时间窗口: {start_dt:%Y-%m-%d %H:%M:%S} ~ {latest_dt:%Y-%m-%d %H:%M:%S} UTC")
    print(f"[INFO] 时长: {(latest_ts - start_ts)/1000/60:.1f} 分钟")

    # 2) 采集关键参数
    param_ids = list(PARAM_MAP.keys())
    query = {
        "deviceId": DEVICE_ID,
        "timeStamp": {"$gte": start_ts, "$lte": latest_ts},
        "paramId": {"$in": param_ids},
    }
    print(f"[INFO] 查询参数 paramId: {param_ids}")
    cursor = coll.find(query, {"_id": 0, "paramId": 1, "value": 1, "timeStamp": 1, "unitName": 1, "name": 1})

    # 3) 按 timeStamp 分组（pivot）
    #    同一时刻的多条参数聚合到一行
    rows = {}          # ts -> {param: value}
    units = {}         # param -> unit (取最后见到的)
    total_raw = 0
    for doc in cursor:
        total_raw += 1
        ts = int(doc["timeStamp"])
        pid = doc["paramId"]
        pname = PARAM_MAP.get(pid)
        if not pname:
            continue
        v = to_float(doc.get("value"))
        rows.setdefault(ts, {})[pname] = v
        if doc.get("unitName"):
            units[pname] = doc["unitName"]

    print(f"[INFO] 原始记录数: {total_raw}")
    print(f"[INFO] 去重时间点数(行): {len(rows)}")
    print(f"[INFO] 单位: {units}")

    if not rows:
        print("[ERROR] 该时间窗口无数据"); sys.exit(1)

    # 4) 排序并计算 ΔP、MP
    sorted_ts = sorted(rows.keys())
    samples = []   # list of dict
    for ts in sorted_ts:
        r = rows[ts]
        # ΔP：优先用设备直接测量的 DrivePress，否则用 Pplat - PEEP
        dp = r.get("DrivePress", float("nan"))
        if math.isnan(dp):
            plat = r.get("Pplat", float("nan"))
            peep = r.get("PEEP", float("nan"))
            if not math.isnan(plat) and not math.isnan(peep):
                dp = plat - peep
        # MP = 0.098 × RR × VT_L × (PIP − 0.5×ΔP)
        # RR 优先用 ftotal(总频率), 回退 PR(自主频率)
        rr = r.get("ftotal", float("nan"))
        if math.isnan(rr):
            rr = r.get("PR", float("nan"))
        vte = r.get("Vte", float("nan"))      # mL
        pip = r.get("PIP", float("nan"))
        mp = float("nan")
        if not any(math.isnan(x) for x in [rr, vte, pip, dp]):
            vt_l = vte / 1000.0
            mp = 0.098 * rr * vt_l * (pip - 0.5 * dp)
        dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
        r["ts"] = ts
        r["dt"] = dt
        r["dP"] = dp
        r["MP"] = mp
        samples.append(r)

    # 5) 过滤掉 ventilator-off 行 (ΔP==0 且 MP==0 视为待机)
    vent_rows = [s for s in samples if not math.isnan(s["dP"]) and s["dP"] > 0]
    standby_rows = [s for s in samples if math.isnan(s["dP"]) or s["dP"] <= 0]
    print(f"[INFO] 通气有效行(dP>0): {len(vent_rows)}  待机行(dP<=0): {len(standby_rows)}")

    if not vent_rows:
        print("[WARN] 无有效通气数据，使用全部行继续")

    # 6) 累积暴露指标
    use_rows = vent_rows if vent_rows else samples

    # ΔP 统计
    dp_vals = [s["dP"] for s in use_rows if not math.isnan(s["dP"])]
    mp_vals = [s["MP"] for s in use_rows if not math.isnan(s["MP"])]

    dp_max = max(dp_vals) if dp_vals else float("nan")
    dp_mean = sum(dp_vals) / len(dp_vals) if dp_vals else float("nan")
    dp_over = [v for v in dp_vals if v > DP_THRESHOLD]
    dp_over_pct = (len(dp_over) / len(dp_vals) * 100) if dp_vals else 0

    mp_max = max(mp_vals) if mp_vals else float("nan")
    mp_mean = sum(mp_vals) / len(mp_vals) if mp_vals else float("nan")
    mp_over = [v for v in mp_vals if v > MP_THRESHOLD]
    mp_over_pct = (len(mp_over) / len(mp_vals) * 100) if mp_vals else 0

    # AUC (超阈值曲线下面积) —— 简化梯形法
    # 采样间隔 ~4s, 转换为分钟
    if len(use_rows) >= 2:
        dp_auc = 0.0
        mp_auc = 0.0
        for i in range(1, len(use_rows)):
            dt_min = (use_rows[i]["ts"] - use_rows[i - 1]["ts"]) / 1000 / 60
            if dt_min <= 0 or dt_min > 1:  # 跳过异常间隔
                continue
            d0, d1 = use_rows[i - 1]["dP"], use_rows[i]["dP"]
            if not math.isnan(d0) and not math.isnan(d1):
                e0 = max(0, d0 - DP_THRESHOLD)
                e1 = max(0, d1 - DP_THRESHOLD)
                dp_auc += (e0 + e1) / 2 * dt_min
            m0, m1 = use_rows[i - 1]["MP"], use_rows[i]["MP"]
            if not math.isnan(m0) and not math.isnan(m1):
                e0 = max(0, m0 - MP_THRESHOLD)
                e1 = max(0, m1 - MP_THRESHOLD)
                mp_auc += (e0 + e1) / 2 * dt_min
    else:
        dp_auc = mp_auc = 0.0

    # 累积机械能 (J) = Σ MP × dt_min
    cum_energy = 0.0
    if len(use_rows) >= 2:
        for i in range(1, len(use_rows)):
            dt_min = (use_rows[i]["ts"] - use_rows[i - 1]["ts"]) / 1000 / 60
            if 0 < dt_min <= 1:
                m = use_rows[i]["MP"]
                if not math.isnan(m):
                    cum_energy += m * dt_min

    # 7) 输出报告
    print("\n" + "=" * 60)
    print("  ΔP / MP 累积暴露 - 真实数据计算结果")
    print("=" * 60)
    print(f"\n  数据源: MongoDB {MONGO_URI} / {DB_NAME}.{COLL}")
    print(f"  设备: {DEVICE_ID}")
    print(f"  窗口: {start_dt:%H:%M:%S} ~ {latest_dt:%H:%M:%S} UTC  ({(latest_ts-start_ts)/1000/60:.0f}min)")
    print(f"  采样间隔: ~{(sorted_ts[1]-sorted_ts[0])/1000:.0f}s" if len(sorted_ts)>1 else "")
    print(f"  有效通气点: {len(use_rows)}")

    print(f"\n  --- ΔP (驱动压, 阈值 {DP_THRESHOLD} cmH2O) ---")
    print(f"  最大值:   {dp_max:.1f} cmH2O")
    print(f"  平均值:   {dp_mean:.1f} cmH2O")
    print(f"  超阈值:   {len(dp_over)} 点 ({dp_over_pct:.1f}%)")
    print(f"  AUC超标:  {dp_auc:.1f} cmH2O·min")

    print(f"\n  --- MP (机械功率, 阈值 {MP_THRESHOLD} J/min) ---")
    print(f"  最大值:   {mp_max:.1f} J/min")
    print(f"  平均值:   {mp_mean:.1f} J/min")
    print(f"  超阈值:   {len(mp_over)} 点 ({mp_over_pct:.1f}%)")
    print(f"  AUC超标:  {mp_auc:.1f} J/min·min")
    print(f"  累积能量: {cum_energy:.1f} J")

    # 风险评级
    risk = 0
    if dp_max > DP_THRESHOLD or mp_max > MP_THRESHOLD:
        risk = max(risk, 2)
    if dp_over_pct > 20 or mp_over_pct > 20:
        risk = max(risk, 3)
    if dp_over_pct > 50 or mp_over_pct > 50:
        risk = max(risk, 4)
    labels = ["L1 正常", "L1 正常", "L2 关注", "L3 警告", "L4 危险"]
    print(f"\n  >>> 综合风险评级: {labels[risk]}")

    # 8) 保存 CSV 供检查
    out_csv = r"C:\Users\lwang\OneDrive\Desktop\智能呼吸机-最快落地开发规划\输出\real_data_timeseries.csv"
    with open(out_csv, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "dt_utc", "DrivePress", "Pplat", "PEEP", "PIP", "ftotal", "PR", "Vte", "dP_calc", "MP_calc"])
        for s in use_rows:
            ft = s.get('ftotal', float('nan'))
            pr = s.get('PR', float('nan'))
            w.writerow([
                s.get("ts", ""),
                s.get("dt", datetime.now()).strftime("%H:%M:%S"),
                f"{s.get('DrivePress', float('nan')):.1f}" if not math.isnan(s.get('DrivePress', float('nan'))) else "",
                f"{s.get('Pplat', float('nan')):.1f}" if not math.isnan(s.get('Pplat', float('nan'))) else "",
                f"{s.get('PEEP', float('nan')):.1f}" if not math.isnan(s.get('PEEP', float('nan'))) else "",
                f"{s.get('PIP', float('nan')):.1f}" if not math.isnan(s.get('PIP', float('nan'))) else "",
                f"{ft:.0f}" if not math.isnan(ft) else "",
                f"{pr:.0f}" if not math.isnan(pr) else "",
                f"{s.get('Vte', float('nan')):.0f}" if not math.isnan(s.get('Vte', float('nan'))) else "",
                f"{s.get('dP', float('nan')):.1f}" if not math.isnan(s.get('dP', float('nan'))) else "",
                f"{s.get('MP', float('nan')):.2f}" if not math.isnan(s.get('MP', float('nan'))) else "",
            ])
    print(f"\n[OK] 时间序列已保存: {out_csv}")

    # 9) 保存 JSON 摘要
    summary = {
        "device": DEVICE_ID,
        "window_start": start_dt.isoformat(),
        "window_end": latest_dt.isoformat(),
        "duration_min": (latest_ts - start_ts) / 1000 / 60,
        "sample_interval_s": (sorted_ts[1] - sorted_ts[0]) / 1000 if len(sorted_ts) > 1 else None,
        "valid_vent_points": len(use_rows),
        "dp": {"max": dp_max, "mean": dp_mean, "threshold": DP_THRESHOLD,
               "over_count": len(dp_over), "over_pct": dp_over_pct, "auc": dp_auc},
        "mp": {"max": mp_max, "mean": mp_mean, "threshold": MP_THRESHOLD,
               "over_count": len(mp_over), "over_pct": mp_over_pct, "auc": mp_auc},
        "cumulative_energy_j": cum_energy,
        "risk_level": labels[risk],
    }
    out_json = r"C:\Users\lwang\OneDrive\Desktop\智能呼吸机-最快落地开发规划\输出\real_data_summary.json"
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)
    print(f"[OK] 摘要已保存: {out_json}")
    print("\n[DONE] 数据采集与计算完成")


if __name__ == "__main__":
    main()
