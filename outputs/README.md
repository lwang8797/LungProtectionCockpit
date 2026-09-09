# 肺保护驾驶舱（Lung Protection Cockpit）

智能呼吸机 ΔP（驱动压）/ MP（机械功）累积暴露实时监控服务。
后端 FastAPI + MongoDB，前端单文件 HTML（触摸屏 15 寸 WebUI），通过 REST + WebSocket 推送。

## 目录结构

```
outputs/
├── cockpit_frontend.html          # 前端驾驶舱（单文件，无构建）
├── lung_protection_cockpit/       # 后端包
│   ├── config.py                  # MongoDB 连接、参数映射、临床阈值
│   ├── collector.py               # 原始数据读取（measure_param）
│   ├── calculator.py              # ΔP/MP 计算、风险分级
│   ├── aggregator.py              # 1 分钟聚合（metrics_1min）+ 预警生成（cockpit_alerts）
│   ├── api.py                     # REST 端点 + WebSocket 推送
│   └── main.py                    # 启动入口（serve / backfill / aggregate / all）
├── scripts/                       # 脚本与测试
│   ├── test_alert_ack.py          # 预警确认持久化回归测试（Python，后端 E2E）
│   ├── test_alert_ack_frontend.js # 预警确认回归测试（Node，前端逻辑 E2E）
│   └── reset_alert_acks.py        # 测试复位工具
├── docs/                          # 需求 / 设计 / 实施方案文档
├── .venv/                         # Python 虚拟环境（3.12）
└── 启动后端.bat                   # 一键启动（端口 8090）
```

## 启动

```bash
# 方式一：双击 outputs/启动后端.bat（内置 COCKPIT_PORT=8090）
# 方式二：命令行
cd outputs
set COCKPIT_PORT=8090
.venv\Scripts\python.exe -m lung_protection_cockpit.main serve
```

浏览器打开 <http://localhost:8090/>。

> 8080 端口被本机 `ApplicationWebServer.exe` 长期占用，故默认改用 8090。
> 当前在线设备 `1787816609`，可用环境变量 `COCKPIT_DEVICE_ID` 覆盖。

## API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/health` | 健康检查 |
| GET | `/api/overview` | 总览仪表盘 |
| GET | `/api/dp/trend` | ΔP 时间序列 |
| GET | `/api/mp/trend` | MP 时间序列 |
| GET | `/api/risk-map` | ΔP-MP 二维风险散点 |
| GET | `/api/alerts` | 预警事件列表（含确认状态） |
| POST | `/api/alerts/{id}/ack` | **确认单条预警（持久化）** |
| POST | `/api/alerts/ack-all` | **批量确认全部活动中预警** |
| GET | `/api/metrics/1min` | 1 分钟聚合明细 |
| GET | `/api/analysis` | URS FR-05 斜率 + CUSUM + FR-06 G0-G3 防抖总评 |
| GET | `/api/shift-summary` | URS 07-4 交接班摘要（8/12/24h，服务端口径） |
| WS | `/ws` | 实时推送总览（2s 间隔） |

## 预警生命周期

预警写入 `cockpit_alerts` 集合，状态为**两态**：

| 状态 | 判定 | 前端显示 |
| --- | --- | --- |
| 活动中 | `active = true` | `● 活动中` + 「确认」按钮 |
| 已确认 | `active = false` 且有 `acknowledged_by` | `✓ 已确认 · 操作员 HH:MM` |
| 已恢复 | `active = false` 且无确认人 | `已恢复`（历史数据兜底） |

**确认状态持久化到 MongoDB**，刷新页面不会丢失。确认会记录
`acknowledged_by` / `acknowledged_at`（毫秒时间戳）/ `acknowledged_at_iso`，满足操作留痕要求。

历史预警（本功能上线前写入）没有 `active` 字段，`GET /api/alerts` 在序列化时按「活动中」兜底，
因此旧预警同样可以被确认。

## 测试

```bash
cd outputs

# 后端 E2E：起临时服务，验证 ACK 后重新拉取仍为已确认（测试完自动还原数据）
.venv\Scripts\python.exe scripts/test_alert_ack.py

# 复位预警状态（前端测试前置条件）
.venv\Scripts\python.exe scripts/reset_alert_acks.py

# 前端 E2E：抽取 HTML 中真实 alert 函数，在 DOM 桩上跑「确认 -> 刷新 -> 重新拉取」
node scripts/test_alert_ack_frontend.js --spawn
```

## 已知约束

- MongoDB 为单机（standalone），**变更流不可用**，实时推送靠 2s 轮询最新批次。
  若需亚秒级推送，需将 192.168.1.100 升为副本集（`rs.initiate()`）。
- 响应出口统一走 `_clean_nan()`：`metrics_1min` 中的 NaN/Inf 会让 Starlette
  `JSONResponse(allow_nan=False)` 直接 500。新增聚合字段务必接入清洗。
- 预警去重窗口 5 分钟（按 `risk_level` + `category`），确认只针对单条事件，
  不会阻止后续新的风险升级再次产生预警。

## URS 新需求实施进度（2026-09-09 起）

**详细清单与连库实测结论**：`outputs/docs/URS新需求覆盖对照清单.md`。

| 模块 | 状态 | 关键变化 |
| :--- | :--- | :--- |
| FR-02 ΔP/MP 双轨口径 | ✅ | `Vt=Vti`（回退 Vte）、`Pplat` 可信性校验（需 PEEP<Pplat<Ppeak 否则降级 *Dyn*），`dp_source`/`mp_source` 实时+全程同源 |
| FR-04 剂量计核心 | ✅ | 真实时间积分（不再跳过大区间）、≤4h 前向填充 / >4h 记断流、`TAT`/`AUC`/`PTA`/`通气时长`/`断流时长` 全量补齐 |
| FR-04 24h 滚动窗口 | ✅ | `cumulative.window.{dp_tat_hours, mp_tat_hours, dcr, dcr_raw, dcr_low, compliance_*}`；DCR<85% 自动提示置信度降级 |
| FR-04 数据完整率 DCR | ✅ | `dcr` = 有效通气时长 / 窗口时长（含填充）；`dcr_raw` = 原始上报密度 |
| CRS 顺应性分层 | ✅ | 全程与窗口**同口径**（按有效通气时长加权滚动），避免前后分层结论不一致 |
| *Dyn* 降级标记 | ✅ | 前端实时仪表 + 累积卡均显示；计算引擎 `dp_source/mp_source` 实时+全程同步 |

## URS 07「界面呈现与交互」实施（2026-09-09，commit 2c3efa9 / 1090133 / fe77fe8 / 2101671）

> 用户确认方案：①双环满量程用**分层 L4 阈值**；②**移除**瞬时半圆表改紧凑数字卡；③交接班汇总**新增后端接口**。

| URS 07 | 交付 | 说明 |
| :--- | :--- | :--- |
| 1 累积剂量计双环表 | ✅ `drawDosimeter` | 外环 ΔP / 内环 MP，绿黄橙红四色；满环=本层 L4（ΔP 6h；MP 高顺应 6h / 低顺应 24h）；中央显示 TAT(h) + AUC。原 `dpGauge`/`mpGauge` 半圆表已移除，瞬时值改紧凑数字卡 |
| 2 时变双轴折线热图 | ✅ `drawTrend` 改造 | 横轴改**真实时钟**（按 `ts` 线性映射，断流自然留白）；阈值红阴影（面积=AUC）；**CUSUM 基线漂移标记旗**消费 `/api/analysis` 的 `cusum.*.change_points`（`side`=up/down），后端不可用时回退本地启发式 |
| 3 顺应性分层标签卡 | ✅ `renderComplianceCard` | 显示 CRS 数值、分组（高顺应性肺·剂量敏感型 / 低顺应性肺·窄带耐受型）、本层 MP 安全上限（18 / 20 J/min）与该层 L3/L4 阈值 |
| 4 交接班摘要与导出 | ✅ `/api/shift-summary` | 后端新增（8/12/24h）：最高暴露、超标总时长、AUC 增量、斜率方向、预警记录；前端卡片 + 导出 CSV（含 BOM 防中文乱码）；两个占位「导出 CSV」按钮接真实实现 |

**未实施 / 待定**：FR-05 GBTM 轨迹归属（通气第 4 天回溯）、俯卧位标记、交接单 HTML/PDF 版式。

**P1 实施进度（2026-09-09 起，commit b93daa6）**：

> 设计口径：用户指示 **P1 严格按 URS 原文设计**，不受当前稀疏库数据束缚；测试用
> **生成器造连续分钟数据**验证，真实验证待真实设备接入连续数据后进行。

| URS | 交付 | 验证方式 |
| :--- | :--- | :--- |
| FR-05 滑动斜率 | `analyzer.sliding_slopes`：1h/6h/24h 三窗回归 β（窗内点<N_min 返回 insufficient） | `scripts/test_analyzer.py` 直线还原 + ramp 场景 |
| FR-05 CUSUM 变化点 | `analyzer.cusum_track`：双侧 S±、k=2、h=5σ、>4h 断流重置、同侧 60min 冷却去重 | step 平移检出 up 变化点；断流不崩溃 |
| FR-06 G0-G3 矩阵 + 防抖 | `analyzer.DebounceGradeEngine`：等级=max(TAT,瞬时持续)；G1≥60/G2≥30/G3≥15 持续确认；回落≥10min 解除（仅瞬时维度）；ΔP≥20 G3 专用游程；<15min 硬地板不响 | 防抖触发/解除/伪差拒绝，见 `scripts/demo_p1.py` |
| 后端接入 | `/api/analysis`：返回 grade{G0-G3,stratum,TAT,sustain,events} + slope + cusum | HTTP 200 + 字段契约对齐前端 |
| 前端 | 总览「URS 趋势分析」卡（G0-G3 徽章+三窗斜率表）、常驻免责声明（NFR-03）、ΔP/MP 趋势 72h 档 | 页面 200；前端 JS `node --check` 通过 |
| 测试数据生成器 | `scripts/gen_urs_testdata.py`（16 参数/批、与 `measure_param` 读结构一致，可 scenario/断流/rows） | 直喂 `_compute_analysis` 全链路 |

**P1 测试运行**：
```
.venv\Scripts\python.exe scripts/test_analyzer.py   # 引擎 19 项断言
.venv\Scripts\python.exe scripts/demo_p1.py          # 端到端演示（生成数据）
```

**约束**：

- 用户指定「波形数据可靠但优先走固定公式」，当前仅用 Gattinoni + 动态式，未做波形
  通道积分；后续如需 ∫Paw·dV 精确算法可启用 `wave_data`（25Hz, 6 通道）。
- 仅针对单设备，无需考虑 32 床并发。旧设备 `ATVIPVTEST1` 在 `history-data` 库的
  `measure_param` 已**清空**（只剩 `1787816609` 的 480 条），不再回退。
- 数据极度稀疏：当前设备最长 14h 上报间隔 → DCR=11~33%，提示置信度降级是常态。

## 模拟数据演示（真实数据稀疏时预览 URS 效果）

真实设备数据稀疏（几十分钟一批），双环剂量计、累积暴露、24h 滚动窗口、滑动斜率/CUSUM
变化点、G0-G3 分级防抖、顺应性分层这些效果在稀疏数据下几乎看不出。用**模拟生成的连续分钟
数据**（结构与 `measure_param` 完全一致，写入独立模拟设备 `SIM900000001`，不污染真实数据）
即可全链路预览。

**一键演示（推荐）**：双击 `outputs\模拟数据演示.bat`（自动清理旧模拟 → 播种最近 24h
`surge` 越限场景 → 用模拟设备启动后端端口 8090），浏览器打开 `http://localhost:8090/`。

**分步命令行**：

```
REM 1) 播种最近 48h 场景（surge=越限累积明显；step=后段抬到 ΔP≥15；ramp=缓慢漂移；lowcomp=低顺应性）
.venv\Scripts\python.exe scripts\seed_sim_device.py --hours 48 --scenario surge
.venv\Scripts\python.exe scripts\seed_sim_device.py --hours 48 --scenario step --step-after 360
.venv\Scripts\python.exe scripts\seed_sim_device.py --hours 48 --scenario ramp --ramp 0.8
.venv\Scripts\python.exe scripts\seed_sim_device.py --hours 24 --scenario lowcomp

REM 2) 用模拟设备启动后端
set COCKPIT_DEVICE_ID=SIM900000001 && set COCKPIT_PORT=8090
.venv\Scripts\python.exe -m lung_protection_cockpit.main all --hours 48

REM 3) （可选，另一个窗口）数值实时跳动——每 5s 追加 1 分钟批次
.venv\Scripts\python.exe scripts\sim_live_feed.py --scenario surge --interval 5

REM 清理模拟数据
.venv\Scripts\python.exe scripts\seed_sim_device.py --clean --device SIM900000001
```

> 各场景能看到的重点：`surge` → 双环剂量计快速填充 + 等级升到 G2/G3；`step` →
> ΔP 越过 15 后触发越限预警 + 分级确认；`ramp` → 趋势斜率上行 + CUSUM 漂移旗；
> `lowcomp` → 顺应性分层切到「低顺应性·窄带耐受」，MP 安全上限降为 20。
