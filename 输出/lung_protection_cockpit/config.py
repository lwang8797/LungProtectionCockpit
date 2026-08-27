# -*- coding: utf-8 -*-
"""
config.py - 全局配置：MongoDB 连接、参数映射、阈值
"""

# ── MongoDB 连接 ──
MONGO_URI = "mongodb://192.168.1.100:27017"
MONGO_DB = "data-services-prod"
COLL_RAW = "measure_param"        # 原始参数集合
COLL_1MIN = "metrics_1min"         # 1分钟聚合结果（本服务创建）
COLL_ALERTS = "cockpit_alerts"     # 预警事件（本服务创建）
COLL_WORK_MODE = "work_mode"       # 通气模式集合（仅在变化时写入）

DEVICE_ID = "ATVIPVTEST1"

# ── 参数 paramId -> 标准化名 ──
# 注意：PR(128) 是患者自主呼吸频率，测试环境恒为"---"
#       ftotal(113) 是总呼吸频率（机控+自主），用于 MP 公式中的 RR
PARAM_MAP = {
    101: "PIP",        # Ppeak 峰压
    102: "Pplat",      # 平台压
    103: "Pmean",      # 平均压
    104: "PEEP",       # 呼末正压
    106: "Vte",        # 呼出潮气量 mL
    107: "FiO2",       # 吸入氧浓度 %
    110: "Vti",        # 吸入潮气量 mL
    111: "MVe",        # 分钟通气量
    113: "ftotal",     # 总呼吸频率 bpm
    114: "fspont",     # 自主呼吸频率 bpm
    116: "Cdyn",       # 动态顺应性 mL/cmH2O
    118: "WOB",        # 呼吸做功 J/L
    128: "PR",         # 患者自主频率（常无效）
    160: "DrivePress", # 驱动压 ΔP（设备直读）
}

# MP 公式所需的 paramId 子集
MP_PARAM_IDS = [101, 106, 113, 160]   # PIP, Vte, ftotal, DrivePress
ALL_PARAM_IDS = list(PARAM_MAP.keys())

# ── 临床阈值（单值维度，循证）──
DP_THRESHOLD = 15.0   # cmH2O  (Amato 2015 NEJM, n=3,562；10/14 篇引用)
MP_THRESHOLD = 17.0    # J/min  (Serpa Neto 2018 / Urbankowski 2025 综述 14-18 J/min)

# ── 累积暴露阈值（双轨制·基于"高暴露小时数"）──
# 依据：水相 ΔP/MP 暴露累积文献精读（2026-08-27）。
#   文献最小时间单位=小时（Lijovic 2026 / Tan 2024），无"连续 N 分钟"直接支持。
#   本系统 1 分钟聚合：高暴露分钟数 / 60 = 高暴露小时数。
#   ⚠ 阈值仍待临床最终确认，但临床依据较单值维度更充分，可在「设置」页随时修改。
#
# 轨道 A：ΔP（阈值统一 15 cmH2O，Amato 2015）
CUM_DP_OVER_HOURS_L3 = 2.0     # 累积高暴露 ≥2h → L3 警告（Lijovic 2026 高顺应性类推）
CUM_DP_OVER_HOURS_L4 = 6.0     # 累积高暴露 ≥6h → L4 危险（保守默认）
#
# 轨道 B：MP（顺应性分层，Lijovic 2026）
COMPLIANCE_STRATUM_THRESHOLD = 32.7   # mL/cmH2O，队列中位数分界（CRS = VT_L / ΔP）
# 高顺应性（CRS > 32.7）：MP ≥ 18 且 累积高暴露 ≥ 2h → 报警
MP_HIGH_STRATUM_THRESHOLD = 18.0
CUM_MP_OVER_HOURS_L3_HIGH = 2.0
CUM_MP_OVER_HOURS_L4_HIGH = 6.0
# 低顺应性（CRS ≤ 32.7）：MP ≥ 20 且 累积高暴露 ≥ 12h → 报警
# （Lijovic：低顺应性风险局限于窄带，无累积伤害证据 → 用更长窗口）
MP_LOW_STRATUM_THRESHOLD = 20.0
CUM_MP_OVER_HOURS_L3_LOW = 12.0
CUM_MP_OVER_HOURS_L4_LOW = 24.0

# ── 风险评级 ──
RISK_LABELS = {
    1: "L1 正常",
    2: "L2 关注",
    3: "L3 警告",
    4: "L4 危险",
}

# ── 采样间隔（秒，用于插值/AUC 计算） ──
SAMPLE_INTERVAL_S = 4.0

# ── 默认查询窗口 ──
DEFAULT_WINDOW_HOURS = 2
