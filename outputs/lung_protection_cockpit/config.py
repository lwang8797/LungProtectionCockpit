# -*- coding: utf-8 -*-
"""
config.py - 全局配置：MongoDB 连接、参数映射、阈值
"""

import os

# ── MongoDB 连接 ──
MONGO_URI = "mongodb://192.168.1.100:27017"
MONGO_DB = "data-services-prod"
COLL_RAW = "measure_param"        # 原始参数集合
COLL_1MIN = "metrics_1min"         # 1分钟聚合结果（本服务创建）
COLL_ALERTS = "cockpit_alerts"     # 预警事件（本服务创建）
COLL_WORK_MODE = "work_mode"       # 通气模式集合（仅在变化时写入）

# 当前呼吸机设备。可用环境变量 COCKPIT_DEVICE_ID 临时覆盖。
# 注（2026-09-09 实测）：measure_param 全集合现仅剩 1787816609 的 480 条（30 批 × 16 参数，
#   跨度 2026-08-27 07:44 ~ 08-28 01:46 UTC）。历史设备 ATVIPVTEST1 的数据已被平台侧清理，
#   count = 0（history-data 库 measure_param_all 亦只有 1787816609 的 480 条）。
#   两台设备 paramId 方案一致，若 ATVIPVTEST1 数据回填，改这里即可切换。
DEVICE_ID = os.environ.get("COCKPIT_DEVICE_ID", "1788932533")
KNOWN_DEVICES = ["1787816609", "ATVIPVTEST1"]

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
# Vt 由 Vti(110) 提供（回退 Vte(106)），RR 由 ftotal(113) 提供，故两者都要采集。
MP_PARAM_IDS = [101, 102, 104, 106, 110, 113]
ALL_PARAM_IDS = list(PARAM_MAP.keys())

# ── 参数口径（2026-09-09 用户确认，勿擅自改动）──
# RR  : 固定用 ftotal(113)（总呼吸频率）。不再回退 PR(128)。
#       注：已发现 Vte×RR/1000=12.07 L/min 与设备上报 MVe=8.0 L/min 不一致，
#           经确认仍以 ftotal 为准。
# VT  : 优先 Vti(110)（吸入潮气量），缺失时回退 Vte(106)。
VT_SOURCE_KEYS = ["Vti", "Vte"]
RR_SOURCE_KEYS = ["ftotal"]

# Pplat 可信性判据（用户确认：Pplat 不可无条件信任，Ppeak 更可信）
# 只有同时满足 PEEP < Pplat < Ppeak 时才认定平台压有效、可用静态驱动压；
# 否则一律降级为动态 ΔP = Ppeak − PEEP，并在界面打 *Dyn* 标记。
# 实测曾出现 Pplat=25.0 == PIP=25.0 的批次，此类必须降级。
PPLAT_STRICT_ORDER = True    # 要求 Pplat 严格小于 Ppeak

# ── 缺失值插补 / 断流（URS FR-01）──
# 通气参数缺失 ≤4h 采用前向填充计入累积；>4h 触发「数据断流」标记，
# 累计剂量计算暂停（超出 4h 的部分不计入暴露），并记录断流时长。
MAX_FORWARD_FILL_MIN = 240.0     # 4 h
# 数据完整率 DCR 低于此值在界面提示置信度降级
DCR_LOW_THRESHOLD = 85.0         # %

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
