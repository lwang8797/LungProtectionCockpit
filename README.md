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

- **ΔP / MP 计算**：驱动压（直读优先 / Pplat−PEEP 备用）、机械功率（Gattinoni 公式），逐点与分钟级聚合。
- **累积暴露报警（双维度合并风险）**：总览页风险评级 = `max(瞬时单值维度, 累积暴露维度)`。
  - 瞬时维度：ΔP / MP 是否越过单值阈值（ΔP≥15、MP≥17，循证）。
  - 累积维度：通气全程滚动累计的机械能 / ΔP 超阈 AUC / MP 超阈 AUC，越过 24h 保守默认阈值（L3 / L4）即升级。
  - 累积暴露阈值（机械能 / ΔP AUC / MP AUC 的 L3、L4）集中在 `输出/lung_protection_cockpit/config.py`，并在前端「设置」页可随时调整（⚠ 待临床/医师确认）。
  - 后端预警区分 `category="threshold"`（单值越限）与 `category="cumulative"`（累积越限），按 (risk_level, category) 去重、互不覆盖。

## 测试

```bash
# 离线纯逻辑测试（无需 MongoDB / 网络）：累积维度评级与合并风险
cd 输出
PYTHONPATH=. python scripts/test_cumulative_logic.py

# 端到端 API 测试（需先启动服务 + MongoDB）
# 见 scripts/test_api.py
```

## 快速开始

```bash
# 安装前端依赖
npm install

# 运行 Python 后端依赖
pip install -r 输出/lung_protection_cockpit/requirements.txt
```
