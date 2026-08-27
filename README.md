# 肺保护驾驶舱

基于真实呼吸机数据构建的「肺保护驾驶舱」项目，聚焦 ΔP（驱动压）与 MP（机械功）累积暴露的可视化与监测，面向 15 寸触摸屏 WebUI 场景。

## 目录结构

- `输出/` — 项目产出物（按类别分类存放）
  - `输出/lung_protection_cockpit/` — 后端计算与采集模块（Python）
  - `输出/scripts/` — 数据处理、文档与前端生成脚本（Python）
  - `输出/*.html` — 前端驾驶舱页面、产品原型、实施方案
  - `输出/*.docx` / `输出/*.json` / `输出/*.csv` — 设计/需求文档与真实数据样例
- `文档/` — 智能呼吸机规划相关文档（一页图、功能规划表、相关性、落地方向）

## 环境依赖

- 前端：见 `package.json`（Node 生态）
- 后端/脚本：见 `输出/lung_protection_cockpit/requirements.txt`（Python）

## 版本管理

本仓库使用 Git 进行版本管理。忽略项见 `.gitignore`（含 `node_modules/`、`__pycache__/`、`.workbuddy/` 等）。

## 核心能力

- **ΔP / MP 计算**：驱动压（静态优先 Pplat−PEEP，回退设备直读 DrivePress，再回退 Ppeak−PEEP）、机械功率（Gattinoni 公式 `0.098·RR·VT·(Ppeak−0.5·ΔP)`），逐点与分钟级聚合。
- **累积暴露报警（双轨制 · 双维度合并风险）**：总览页风险评级 = `max(瞬时单值维度, 累积暴露维度)`。
  - **瞬时维度**：ΔP / MP 是否越过单值阈值（ΔP≥15、MP≥17，循证）；超阈占比>20%→L3、>50%→L4。
  - **累积维度（高暴露小时数 + 顺应性分层）**，依据 2026-08-27 文献精读（水相）：
    - 度量 = **高暴露小时数**（高暴露分钟数 ÷ 60，1 分钟聚合累加）；文献最小时间单位=小时。
    - ΔP 轨道：ΔP≥15 累积高暴露 **≥2h → L3**、≥6h → L4。
    - MP 轨道按**顺应性分层**（CRS = Vte / ΔP，分界 32.7 mL/cmH₂O）：
      - 高顺应性（CRS>32.7）：MP≥18 且 高暴露 ≥2h → L3、≥6h → L4；
      - 低顺应性（CRS≤32.7）：MP≥20 且 高暴露 ≥12h → L3、≥24h → L4。
    - 辅助指标：超阈 AUC（ΔP / MP）、总机械能（kJ）、% 时间超阈，用于展示与核查。
  - 累积暴露阈值（高暴露小时数 / MP 分层阈值 / 顺应性分界）集中在 `输出/lung_protection_cockpit/config.py`，并在前端「设置」页可随时调整（⚠ 待临床/医师最终确认）。
  - 后端预警区分 `category="threshold"`（单值越限）与 `category="cumulative"`（累积越限），按 (risk_level, category) 去重、互不覆盖。

## 启动方式

### 1) 后端（FastAPI 服务 + 聚合）

依赖：Python 3.10+，并安装 `输出/lung_protection_cockpit/requirements.txt`（fastapi / uvicorn / pymongo / python-dateutil 等）。

```bash
cd 输出

# 安装依赖（推荐虚拟环境）
pip install -r lung_protection_cockpit/requirements.txt

# 一键启动（回填最近 2h 历史 + 启动 API，监听 0.0.0.0:8080）
PYTHONPATH=. python -m lung_protection_cockpit.main all --hours 2

# 或分步：
PYTHONPATH=. python -m lung_protection_cockpit.main backfill --hours 24   # 回填历史聚合（metrics_1min）
PYTHONPATH=. python -m lung_protection_cockpit.main aggregate              # 持续聚合新数据（守护进程）
PYTHONPATH=. python -m lung_protection_cockpit.main serve                 # 仅启动 API
```

环境变量：`COCKPIT_HOST`（默认 0.0.0.0）、`COCKPIT_PORT`（默认 8080）。
启动后访问：

- 前端驾驶舱页面：`http://localhost:8080/`
- 接口文档（Swagger）：`http://localhost:8080/docs`
- 健康检查：`GET http://localhost:8080/api/health`

数据来源：MongoDB `192.168.1.100:27017 / data-services-prod.measure_param`（见 `config.py`，可按需修改）。

### 2) 前端（独立静态预览，不依赖后端）

`输出/cockpit_frontend.html` 是**自包含**的驾驶舱页面（HTML+CSS+JS 内联），可直接用浏览器打开；若后端已启动，页面会实时拉取 `/api/overview` 等接口并自动通过 WebSocket 刷新。

```bash
# 方式 A：直接用浏览器打开
#   文件管理器双击 输出/cockpit_frontend.html，或 VS Code "Open with Live Server"

# 方式 B：本地起一个静态服务器（任选其一）
npx serve 输出 -l 5173
# 或
python -m http.server 5173 -d 输出
# 然后浏览器打开 http://localhost:5173/cockpit_frontend.html
```

> 说明：本项目前端与后端解耦——后端 `api.py` 通过 `GET /` 也会托管同一份 `cockpit_frontend.html`，因此"方式 1"已包含前端；"方式 2"用于无后端环境下的纯界面预览/调试。

## 测试

```bash
# 离线纯逻辑测试（无需 MongoDB / 网络）：累积维度评级、顺应性分层与合并风险
cd 输出
PYTHONPATH=. python scripts/test_cumulative_logic.py

# 端到端 API 测试（需先启动服务 + MongoDB）
# 见 scripts/test_api.py
```
