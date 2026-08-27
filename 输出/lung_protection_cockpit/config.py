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
DP_THRESHOLD = 15.0   # cmH2O  (Amato 2015 NEJM)
MP_THRESHOLD = 17.0    # J/min  (Chest 2025)

# ── 累积暴露阈值（累积维度，24h 窗口）──
# ⚠ 待临床确认：以下为保守默认建议值，将来可在「设置」页随时调整。
#    单位：机械能 kJ；MP 超阈 AUC kJ；ΔP 超阈 AUC cmH2O·min。
#    仅设 L3（警告）/ L4（危险）两级；低于 L3 视为累积维度无风险（L1）。
CUM_ENERGY_L3_KJ = 50.0     # 全天机械能负荷 ~ 阈值(17 J/min)持续一天≈24.5kJ，取 2 倍作 L3
CUM_ENERGY_L4_KJ = 100.0    # 4 倍作 L4
CUM_MP_AUC_L3_KJ = 1.5      # MP 超阈 AUC：(MP−17) 累积，24h 内每+1J/min≈1.44kJ
CUM_MP_AUC_L4_KJ = 3.0
CUM_DP_AUC_L3 = 1500.0      # ΔP 超阈 AUC：(ΔP−15) 累积，单位 cmH2O·min
CUM_DP_AUC_L4 = 3000.0

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
