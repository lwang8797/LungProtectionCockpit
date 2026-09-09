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

**未实施**（待 P1/P2 批次确认后开发）：FR-05 斜率/CUSUM/GBTM 变化点检测、FR-06
分级报警防抖与自动解除、FR-07 72h/交接班/俯卧位摘要、免责声明 + 脱敏、CSV 导出。

**约束**：

- 用户指定「波形数据可靠但优先走固定公式」，当前仅用 Gattinoni + 动态式，未做波形
  通道积分；后续如需 ∫Paw·dV 精确算法可启用 `wave_data`（25Hz, 6 通道）。
- 仅针对单设备，无需考虑 32 床并发。旧设备 `ATVIPVTEST1` 在 `history-data` 库的
  `measure_param` 已**清空**（只剩 `1787816609` 的 480 条），不再回退。
- 数据极度稀疏：当前设备最长 14h 上报间隔 → DCR=11~33%，提示置信度降级是常态。
