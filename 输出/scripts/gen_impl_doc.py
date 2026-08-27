# -*- coding: utf-8 -*-
"""
生成「肺保护驾驶舱」真实数据实施方案文档 (HTML)
基于从测试环境 MongoDB 采集到的真实数据结构
"""
import os

OUT = r"C:\Users\lwang\OneDrive\Desktop\智能呼吸机-最快落地开发规划\输出\实施方案-肺保护驾驶舱（基于真实数据）.html"

html = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>实施方案 - 肺保护驾驶舱（基于真实数据）</title>
<style>
:root{
  --bg:#07090D; --surface:#151A22; --surface2:#1C2230; --border:#2A3140;
  --text:#E8ECF1; --text2:#9BA3B0; --text3:#6B7280;
  --green:#22C55E; --yellow:#FBBF24; --orange:#F97316; --red:#EF4444; --purple:#A78BFA; --blue:#60A5FA;
  --mono:'Cascadia Code','Fira Code',Consolas,monospace;
}
*{margin:0;padding:0;box-sizing:border-box}
body{background:var(--bg);color:var(--text);font-family:'Microsoft YaHei','PingFang SC',system-ui,sans-serif;line-height:1.7;font-size:14px;padding:40px 20px;max-width:960px;margin:0 auto}
h1{font-size:24px;font-weight:600;margin-bottom:8px;color:var(--text)}
h2{font-size:18px;font-weight:600;margin:32px 0 12px;padding-bottom:8px;border-bottom:1px solid var(--border);color:var(--blue)}
h3{font-size:15px;font-weight:600;margin:24px 0 8px;color:var(--purple)}
p{margin:8px 0;color:var(--text)}
ul,ol{margin:8px 0 8px 20px}
li{margin:4px 0}
code{font-family:var(--mono);background:var(--surface2);padding:2px 6px;border-radius:4px;font-size:13px;color:var(--green)}
pre{background:var(--surface2);border:1px solid var(--border);border-radius:8px;padding:16px;overflow-x:auto;margin:12px 0}
pre code{background:none;padding:0;font-size:12px;line-height:1.5;color:var(--text2)}
table{border-collapse:collapse;width:100%;margin:12px 0;font-size:13px}
th{background:var(--surface2);color:var(--blue);padding:8px 12px;text-align:left;border:1px solid var(--border);font-weight:600}
td{padding:8px 12px;border:1px solid var(--border);color:var(--text)}
tr:nth-child(even){background:var(--surface)}
.card{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:20px;margin:16px 0}
.card-title{font-size:15px;font-weight:600;color:var(--blue);margin-bottom:8px}
.badge{display:inline-block;padding:2px 10px;border-radius:12px;font-size:12px;font-weight:600;margin:0 4px}
.b-green{background:rgba(34,197,94,.15);color:var(--green)}
.b-yellow{background:rgba(251,191,36,.15);color:var(--yellow)}
.b-red{background:rgba(239,68,68,.15);color:var(--red)}
.b-blue{background:rgba(96,165,250,.15);color:var(--blue)}
.metric{display:flex;gap:20px;flex-wrap:wrap;margin:12px 0}
.metric-item{background:var(--surface2);border-radius:8px;padding:12px 20px;text-align:center;min-width:120px}
.metric-val{font-size:22px;font-weight:700;color:var(--green)}
.metric-label{font-size:12px;color:var(--text3);margin-top:4px}
.note{background:rgba(96,165,250,.08);border-left:3px solid var(--blue);padding:12px 16px;border-radius:0 8px 8px 0;margin:12px 0}
.warn{background:rgba(239,68,68,.08);border-left:3px solid var(--red);padding:12px 16px;border-radius:0 8px 8px 0;margin:12px 0}
.success{background:rgba(34,197,94,.08);border-left:3px solid var(--green);padding:12px 16px;border-radius:0 8px 8px 0;margin:12px 0}
.tag{display:inline-block;background:var(--surface2);color:var(--text2);padding:2px 8px;border-radius:4px;font-size:12px;margin:2px}
hr{border:none;border-top:1px solid var(--border);margin:32px 0}
.footer{text-align:center;color:var(--text3);font-size:12px;margin-top:40px;padding-top:20px;border-top:1px solid var(--border)}
</style>
</head>
<body>

<h1>实施方案 — 肺保护驾驶舱（基于真实数据）</h1>
<p style="color:var(--text2)">智能呼吸机 ΔP/MP 累积暴露功能 · 测试环境真实数据验证版</p>
<p style="color:var(--text3);font-size:12px">生成时间：2026-08-26 | 数据源：192.168.1.100 MongoDB data-services-prod</p>

<div class="success">
<strong>验证结论：数据采集与计算管道完全打通。</strong><br>
从 MongoDB <code>measure_param</code> 集合成功采集 24,564 条原始参数记录，透视对齐为 2,047 个时间点，计算 ΔP 和 MP 累积暴露指标全部正常输出。
</div>

<div class="metric">
<div class="metric-item"><div class="metric-val">10.0</div><div class="metric-label">ΔP 最大 (cmH₂O)</div></div>
<div class="metric-item"><div class="metric-val">5.9</div><div class="metric-label">MP 最大 (J/min)</div></div>
<div class="metric-item"><div class="metric-val">204.8</div><div class="metric-label">累积能量 (J)</div></div>
<div class="metric-item"><div class="metric-val">L1</div><div class="metric-label">风险评级</div></div>
</div>

<hr>

<h2>1. 真实数据探测结论</h2>

<h3>1.1 MongoDB 连接信息</h3>
<table>
<tr><th>项目</th><th>值</th><th>说明</th></tr>
<tr><td>主机</td><td><code>192.168.1.100:27017</code></td><td>测试环境，SSH 用户 norco</td></tr>
<tr><td>数据库</td><td><code>data-services-prod</code></td><td>6.4 GB，生产数据</td></tr>
<tr><td>集合</td><td><code>measure_param</code></td><td>7,348,775 条记录</td></tr>
<tr><td>认证</td><td>无（security 未启用）</td><td>bindIp: 0.0.0.0</td></tr>
<tr><td>索引</td><td><code>_id_</code>, <code>deviceId_1_timeStamp_1</code></td><td>仅有 2 个索引</td></tr>
<tr><td>设备</td><td><code>ATVIPVTEST1</code></td><td>当前仅有 1 台测试设备</td></tr>
</table>

<h3>1.2 measure_param 文档结构</h3>
<pre><code>{
  _id: ObjectId("..."),
  paramId: 160,                    // 参数ID（数字）
  deviceId: "ATVIPVTEST1",         // 设备ID
  value: "8",                      // 值（字符串！需 parseFloat）
  name: "DrivePress",              // 参数名
  unitName: "cmH₂O",              // 单位
  module: "",
  timeStamp: Long(1785718725906), // 服务器时间戳（毫秒）← 用于时间查询
  deviceTimeStamp: Long(1754900944000), // 设备时间戳 ← 不可靠（见下）
  _class: "com.yian.mqtt.entity.pojo.MeasureParamEntity"
}</code></pre>

<h3>1.3 关键参数映射表</h3>
<table>
<tr><th>paramId</th><th>name</th><th>标准化名</th><th>单位</th><th>用途</th><th>数据质量</th></tr>
<tr><td>101</td><td>Ppeak</td><td>PIP</td><td>cmH₂O</td><td>峰压 → MP公式</td><td><span class="badge b-green">正常</span></td></tr>
<tr><td>102</td><td>Pplat</td><td>Pplat</td><td>cmH₂O</td><td>平台压 → ΔP备用</td><td><span class="badge b-green">正常</span></td></tr>
<tr><td>104</td><td>PEEP</td><td>PEEP</td><td>cmH₂O</td><td>呼末正压 → ΔP备用</td><td><span class="badge b-yellow">部分"OFF"</span></td></tr>
<tr><td>106</td><td>Vte</td><td>Vte</td><td>mL</td><td>呼出潮气量 → MP公式</td><td><span class="badge b-green">正常</span></td></tr>
<tr><td>110</td><td>Vti</td><td>Vti</td><td>mL</td><td>吸入潮气量</td><td><span class="badge b-green">正常</span></td></tr>
<tr><td>113</td><td>ftotal</td><td>RR</td><td>bpm</td><td>总呼吸频率 → MP公式</td><td><span class="badge b-green">正常</span></td></tr>
<tr><td>114</td><td>fspont</td><td>fspont</td><td>bpm</td><td>自主呼吸频率</td><td><span class="badge b-green">正常</span></td></tr>
<tr><td>116</td><td>Cdyn</td><td>Cdyn</td><td>mL/cmH₂O</td><td>动态顺应性</td><td><span class="badge b-green">正常</span></td></tr>
<tr><td>118</td><td>WOB</td><td>WOB</td><td>J/L</td><td>呼吸做功</td><td><span class="badge b-green">正常</span></td></tr>
<tr><td>128</td><td>PR</td><td>PR</td><td>bpm</td><td>患者自主频率</td><td><span class="badge b-red">恒为"---"</span></td></tr>
<tr><td>160</td><td>DrivePress</td><td>ΔP</td><td>cmH₂O</td><td>驱动压（直读）</td><td><span class="badge b-green">正常</span></td></tr>
</table>

<div class="warn">
<strong>关键数据质量发现：</strong>
<ul>
<li><b>PR (paramId=128) 恒为 "---"</b>：183,833 条记录全部无效值。该参数测量患者自主呼吸频率，测试环境无自主呼吸故恒无效。<b>用 ftotal (paramId=113) 替代</b>作为 MP 公式中的 RR。</li>
<li><b>deviceTimeStamp 不可靠</b>：全库仅 2 个不同的 deviceTimeStamp 值（停在 2025年），设备时钟故障。<b>必须用 server <code>timeStamp</code> 字段</b>做所有时间查询。</li>
<li><b>value 是字符串类型</b>：含 "OFF"、"---" 等非数值标记，需 parseFloat + try/except 处理。</li>
<li><b>采样间隔 ~4 秒</b>：每个参数约 4 秒一条记录，14 个参数 × 4s = 每分钟约 210 条。</li>
<li><b>待机数据</b>：ΔP=0 表示呼吸机待机/未通气，需过滤后计算暴露指标。</li>
</ul>
</div>

<h3>1.4 数据时间范围</h3>
<table>
<tr><th>指标</th><th>值</th></tr>
<tr><td>最早 server timeStamp</td><td>2026-07-27 01:32:41 UTC</td></tr>
<tr><td>最晚 server timeStamp</td><td>2026-08-03 00:58:45 UTC</td></tr>
<tr><td>数据跨度</td><td>约 7 天</td></tr>
<tr><td>有效通气点（2h窗口）</td><td>1,185 / 2,047（58% 时间在通气）</td></tr>
</table>

<hr>

<h2>2. 更新后的计算方案</h2>

<h3>2.1 ΔP（驱动压）计算</h3>
<div class="card">
<div class="card-title">策略：设备直读优先 + 计算备用</div>
<pre><code># 优先使用设备直接测量的 DrivePress
dp = row["DrivePress"]  # paramId=160

# 备用：Pplat - PEEP（当 DrivePress 缺失时）
if isnan(dp):
    dp = row["Pplat"] - row["PEEP"]  # paramId=102 - 104</code></pre>
<p>阈值：<code>ΔP ≥ 15 cmH₂O</code>（Amato 2015 NEJM）</p>
</div>

<h3>2.2 MP（机械功率）计算</h3>
<div class="card">
<div class="card-title">简化机械功率公式（Chi 2025 Chest）</div>
<pre><code>MP = 0.098 × RR × VT_L × (PIP − 0.5 × ΔP)

# 参数来源：
#   RR  = ftotal (paramId=113, bpm)     ← 替代 PR(128)
#   VT  = Vte   (paramId=106, mL → /1000 → L)
#   PIP = Ppeak  (paramId=101, cmH₂O)
#   ΔP  = DrivePress (paramId=160, cmH₂O)

rr = row["ftotal"]      # 总呼吸频率
vt_l = row["Vte"] / 1000.0
pip = row["PIP"]
dp = row["dP"]          # 已计算的 ΔP
mp = 0.098 * rr * vt_l * (pip - 0.5 * dp)</code></pre>
<p>阈值：<code>MP ≥ 17 J/min</code>（Chest 2025）</p>
</div>

<h3>2.3 累积暴露指标</h3>
<table>
<tr><th>指标</th><th>计算方法</th><th>单位</th><th>含义</th></tr>
<tr><td>AUC（超标曲线下面积）</td><td>梯形法 Σ max(0, val−threshold) × dt</td><td>cmH₂O·min / J·min⁻¹·min</td><td>超标累积量</td></tr>
<tr><td>时间超阈占比</td><td>count(val > threshold) / total × 100</td><td>%</td><td>超标时间比例</td></tr>
<tr><td>累积机械能</td><td>Σ MP × dt_min</td><td>J</td><td>总能量输入</td></tr>
<tr><td>风险评级</td><td>L1~L4 四级</td><td>—</td><td>综合风险等级</td></tr>
</table>

<h3>2.4 实测结果（2小时窗口）</h3>
<div class="metric">
<div class="metric-item"><div class="metric-val" style="color:var(--green)">10.0</div><div class="metric-label">ΔP 最大 (cmH₂O)</div></div>
<div class="metric-item"><div class="metric-val" style="color:var(--green)">9.0</div><div class="metric-label">ΔP 均值 (cmH₂O)</div></div>
<div class="metric-item"><div class="metric-val" style="color:var(--green)">0%</div><div class="metric-label">ΔP 超阈占比</div></div>
<div class="metric-item"><div class="metric-val" style="color:var(--green)">5.9</div><div class="metric-label">MP 最大 (J/min)</div></div>
<div class="metric-item"><div class="metric-val" style="color:var(--green)">3.6</div><div class="metric-label">MP 均值 (J/min)</div></div>
<div class="metric-item"><div class="metric-val" style="color:var(--green)">0%</div><div class="metric-label">MP 超阈占比</div></div>
</div>
<div class="note">本次测试数据为保护性通气（低 ΔP 低 MP），所有指标均在安全范围内。在真实患者场景中，当 VT 更大、PEEP 更高、顺应性更差时，ΔP 和 MP 会显著升高，触发风险预警。</div>

<hr>

<h2>3. 后端服务架构</h2>

<h3>3.1 技术栈</h3>
<table>
<tr><th>层</th><th>技术</th><th>说明</th></tr>
<tr><td>数据采集</td><td>Python 3.10+ / pymongo 4.17</td><td>已验证可用，远程已安装</td></tr>
<tr><td>计算引擎</td><td>Python / NumPy</td><td>ΔP/MP 计算 + 累积暴露</td></tr>
<tr><td>API 服务</td><td>FastAPI + Uvicorn</td><td>REST + WebSocket</td></tr>
<tr><td>数据存储</td><td>MongoDB（同库）</td><td>新建 metrics_1min 集合存聚合结果</td></tr>
<tr><td>前端</td><td>单文件 HTML（深色主题）</td><td>已有原型，SVG 原生绘制</td></tr>
</table>

<h3>3.2 模块划分</h3>
<pre><code>lung_protection_cockpit/
├── config.py            # MongoDB 连接、阈值、paramId 映射
├── collector.py         # 数据采集：按时间窗口查询 measure_param
├── calculator.py        # 计算引擎：ΔP/MP + 累积暴露指标
├── aggregator.py        # 1分钟聚合：写入 metrics_1min 集合
├── api.py               # FastAPI 路由：REST + WebSocket
├── realtime.py          # 实时推送：监听新数据 → 计算 → WS推送
└── main.py              # 启动入口</code></pre>

<h3>3.3 数据采集流程</h3>
<pre><code># 1. 按索引查询（利用 deviceId_1_timeStamp_1 索引）
query = {
    "deviceId": DEVICE_ID,
    "timeStamp": {"$gte": start_ts, "$lte": end_ts},
    "paramId": {"$in": [101, 102, 104, 106, 113, 160, ...]}
}
cursor = db.measure_param.find(query)

# 2. 时间戳对齐（pivot）：同一 timeStamp 的多条参数合并为一行
rows = {}  # ts -> {param_name: value}
for doc in cursor:
    ts = doc["timeStamp"]
    pname = PARAM_MAP[doc["paramId"]]
    rows.setdefault(ts, {})[pname] = float(doc["value"])

# 3. 过滤待机行（ΔP=0 视为未通气）
vent_rows = [r for r in rows.values() if r.get("dP", 0) > 0]</code></pre>

<h3>3.4 REST API 设计</h3>
<table>
<tr><th>端点</th><th>方法</th><th>参数</th><th>返回</th></tr>
<tr><td><code>/api/overview</code></td><td>GET</td><td>deviceId, hours=2</td><td>总览页数据（评级+仪表盘）</td></tr>
<tr><td><code>/api/dp/trend</code></td><td>GET</td><td>deviceId, start, end</td><td>ΔP 时间序列</td></tr>
<tr><td><code>/api/mp/trend</code></td><td>GET</td><td>deviceId, start, end</td><td>MP 时间序列</td></tr>
<tr><td><code>/api/risk-map</code></td><td>GET</td><td>deviceId, hours=2</td><td>二维风险图数据</td></tr>
<tr><td><code>/api/alerts</code></td><td>GET</td><td>deviceId, hours=24</td><td>预警事件列表</td></tr>
<tr><td><code>/ws/realtime</code></td><td>WS</td><td>deviceId</td><td>实时 ΔP/MP 推送（每4秒）</td></tr>
</table>

<hr>

<h2>4. MongoDB 数据模型</h2>

<h3>4.1 metrics_1min 集合（新建）</h3>
<pre><code>{
  _id: ObjectId("..."),
  deviceId: "ATVIPVTEST1",
  minute: ISODate("2026-08-03T00:01:00Z"),  // 1分钟桶

  // ΔP 指标
  dp_mean: 9.0,           // 均值 cmH₂O
  dp_max: 10.0,           // 最大值
  dp_min: 8.0,            // 最小值
  dp_over_count: 0,       // 超阈值点数
  dp_over_pct: 0.0,       // 超阈值时间占比 %
  dp_auc: 0.0,            // 本分钟 AUC 增量

  // MP 指标
  mp_mean: 3.6,
  mp_max: 5.9,
  mp_over_count: 0,
  mp_over_pct: 0.0,
  mp_auc: 0.0,

  // 累积（从通气开始累计）
  cumulative_dp_auc: 0.0,   // 累积 ΔP AUC
  cumulative_mp_auc: 0.0,   // 累积 MP AUC
  cumulative_energy: 3.6,   // 累积机械能 J
  vent_duration_min: 1.0,   // 累积通气时间 min

  // 风险
  risk_level: 1,             // L1-L4

  // 原始参数快照（均值）
  pip_mean: 15.0,
  peep_mean: 5.0,
  vt_mean: 235,
  rr_mean: 15,
  plat_mean: 14.0,
  crs_mean: 25,             // Cdyn 顺应性

  created_at: ISODate("...")
}</code></pre>

<h3>4.2 索引建议</h3>
<div class="warn">
<b>当前 measure_param 缺少关键索引</b>，按 name 或 paramId 查询会全表扫描（7.3M 条）。
建议在测试环境创建：
<pre><code>// 高优先级：支持按参数+时间范围查询
db.measure_param.createIndex(
  { deviceId: 1, paramId: 1, timeStamp: -1 },
  { name: "dev_param_time" }
)

// metrics_1min 集合索引
db.metrics_1min.createIndex({ deviceId: 1, minute: -1 })</code></pre>
</div>

<hr>

<h2>5. 实施计划</h2>

<table>
<tr><th>阶段</th><th>内容</th><th>产出</th><th>状态</th></tr>
<tr><td><b>M1</b><br>数据采集+计算</td><td>连接MongoDB、采集measure_param、计算ΔP/MP和累积暴露</td><td>collect_real_data.py<br>real_data_summary.json</td><td><span class="badge b-green">已完成</span></td></tr>
<tr><td><b>M2</b><br>聚合服务</td><td>1分钟定时聚合、写入metrics_1min、累积量滚动</td><td>aggregator.py</td><td><span class="badge b-yellow">待开发</span></td></tr>
<tr><td><b>M3</b><br>REST API</td><td>FastAPI路由：总览/趋势/风险图/预警</td><td>api.py</td><td><span class="badge b-yellow">待开发</span></td></tr>
<tr><td><b>M4</b><br>实时推送</td><td>WebSocket监听新数据→计算→推送前端</td><td>realtime.py</td><td><span class="badge b-yellow">待开发</span></td></tr>
<tr><td><b>M5</b><br>前端集成</td><td>原型HTML对接真实API、实盘数据展示</td><td>驾驶舱WebUI</td><td><span class="badge b-yellow">待开发</span></td></tr>
</table>

<h3>5.1 M1 已完成成果</h3>
<ul>
<li><code>输出/scripts/collect_real_data.py</code> — 完整采集计算脚本，可重复运行</li>
<li><code>输出/real_data_timeseries.csv</code> — 1,185 行时间序列数据</li>
<li><code>输出/real_data_summary.json</code> — 计算结果摘要</li>
<li>验证了数据管道全链路：MongoDB查询 → 透视对齐 → ΔP/MP计算 → 累积暴露指标</li>
</ul>

<h3>5.2 M2 聚合服务设计要点</h3>
<pre><code># 每分钟执行一次，处理上一分钟的原始数据
def aggregate_minute(device_id, minute_start):
    end = minute_start + 60000  # 1min

    # 1. 采集该分钟原始数据
    rows = collect(device_id, minute_start, end)

    # 2. 计算 ΔP/MP
    for row in rows:
        calculate_dp_mp(row)

    # 3. 过滤待机
    vent_rows = [r for r in rows if r["dP"] > 0]

    # 4. 聚合统计
    stats = {
        "dp_mean": mean(r["dP"] for r in vent_rows),
        "dp_max": max(r["dP"] for r in vent_rows),
        "dp_over_pct": count_over / len(vent_rows) * 100,
        "mp_mean": mean(r["MP"] for r in vent_rows),
        ...
    }

    # 5. 累积量滚动（从上一分钟读取累积值）
    prev = db.metrics_1min.find_one(
        {"deviceId": device_id}, sort=[("minute", -1)]
    )
    stats["cumulative_energy"] = (prev["cumulative_energy"] if prev else 0) \
        + stats["mp_mean"] * len(vent_rows) * 4 / 60

    # 6. 写入
    db.metrics_1min.update_one(
        {"deviceId": device_id, "minute": minute_start},
        {"$set": stats}, upsert=True
    )</code></pre>

<hr>

<h2>6. 关键技术决策</h2>

<table>
<tr><th>决策点</th><th>选择</th><th>理由</th></tr>
<tr><td>时间字段</td><td>server <code>timeStamp</code></td><td>deviceTimeStamp 恒停 2025年，不可靠；timeStamp 有索引</td></tr>
<tr><td>RR 来源</td><td><code>ftotal</code> (paramId=113)</td><td>PR(128)恒"---"，ftotal 是总呼吸频率含机控呼吸</td></tr>
<tr><td>ΔP 来源</td><td><code>DrivePress</code> (paramId=160)</td><td>设备直接测量，无需计算；Pplat-PEEP 作备用</td></tr>
<tr><td>待机过滤</td><td>ΔP=0 行排除</td><td>呼吸机未通气时参数无意义，避免拉低均值</td></tr>
<tr><td>非数值处理</td><td>parseFloat + try/except → NaN</td><td>value 字段含 "OFF"/"---" 等字符串标记</td></tr>
<tr><td>查询优化</td><td>deviceId + timeStamp 范围 + paramId $in</td><td>利用现有索引，单次查询 2h≈3秒</td></tr>
</table>

<hr>

<h2>7. 部署方案</h2>

<h3>7.1 后端部署</h3>
<p>后端 Python 服务部署在<b>测试环境机器</b>（192.168.1.100）上，与 MongoDB 同机：</p>
<pre><code># 在 192.168.1.100 上部署
cd /opt/lung-protection-cockpit
pip3 install fastapi uvicorn pymongo
uvicorn api:app --host 0.0.0.0 --port 8080

# 或用 systemd 管理
# /etc/systemd/system/lung-cockpit.service
[Unit]
Description=Lung Protection Cockpit API
After=network.target mongod.service

[Service]
Type=simple
User=norco
WorkingDirectory=/opt/lung-protection-cockpit
ExecStart=/usr/bin/python3 -m uvicorn api:app --host 0.0.0.0 --port 8080
Restart=always

[Install]
WantedBy=multi-user.target</code></pre>

<h3>7.2 前端部署</h3>
<p>原型 HTML 为单文件离线应用，部署方式：</p>
<ul>
<li><b>方案A</b>：由 FastAPI 静态文件服务托管，<code>/cockpit</code> 路由返回 HTML</li>
<li><b>方案B</b>：嵌入呼吸机本地 Web 服务器，直接打开文件</li>
<li><b>方案C</b>：Nginx 反向代理，<code>/api</code> → FastAPI，<code>/</code> → 静态文件</li>
</ul>

<h3>7.3 15寸触摸屏适配</h3>
<ul>
<li>分辨率 1366×768，深色主题</li>
<li>触摸目标 ≥ 44×44px，间距 ≥ 12px</li>
<li>大字体仪表盘数字（28px+），一句话提示（16px）</li>
<li>无 hover 交互，全部改为 tap</li>
</ul>

<div class="footer">
<p>肺保护驾驶舱 · 实施方案（基于真实数据） · 2026-08-26</p>
<p>数据源：MongoDB 192.168.1.100:27017 / data-services-prod.measure_param</p>
</div>

</body>
</html>"""

with open(OUT, "w", encoding="utf-8") as f:
    f.write(html)
print(f"[OK] HTML written to {OUT}")
print(f"[INFO] Size: {os.path.getsize(OUT)} bytes")
