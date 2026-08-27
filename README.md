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

## 快速开始

```bash
# 安装前端依赖
npm install

# 运行 Python 后端依赖
pip install -r 输出/lung_protection_cockpit/requirements.txt
```
