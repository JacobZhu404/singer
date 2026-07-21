---
type: quickstart
title: 快速开始 · 歌者（Gezhe）智能选股系统
description: 项目简介、安装、运行方式，以及文档导航
tags: [stock-screener, quickstart, getting-started]
---

# 歌者（Gezhe）智能选股系统

基于同花顺/东方财富数据的全自动 A 股筛选工具，支持多策略并行扫描、综合评分推荐。

> 命名灵感来自《三体》文明——在资本市场的噪声中，捕捉微弱的秩序信号。

## 系统要求

- **Python 3.11**（系统自带 `python3` 常为 3.7，不可用，必须走项目 venv）
- macOS / Linux / WSL

## 安装与启动

### 方式一：本地一键启动

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
bash start.sh        # 自动装依赖、检查端口、打开浏览器
# 访问 http://127.0.0.1:5188
```

### 方式二：直接 Python 启动

```bash
.venv/bin/python main.py web [--port 5188] [--host 0.0.0.0] [--debug]   # Web 服务
.venv/bin/python main.py screen -s macd_bull strong_stock -m 主板 -n 10 # 命令行筛选
.venv/bin/python main.py list                                          # 列出所有策略
.venv/bin/python main.py screen -s macd_bull --json                    # JSON 输出
```

### 方式三：Docker

```bash
docker compose up --build                                                  # 本地
docker compose -f docker-compose.yml -f docker-compose.alibaba.yml up -d   # 阿里云 ECS
docker compose -f docker-compose.yml -f docker-compose.tencent.yml up -d   # 腾讯云 CVM
docker run -p 5188:5188 gezhe:latest                                       # 生产（Gunicorn）
```

## 文档导航

- [架构概览](architecture/overview.md) — 三层架构、核心模块、关键设计原则
- [实时筛选流程](workflows/screening.md) — 一次筛选从触发到推荐的完整链路
- [数据获取流程](workflows/data-fetch.md) — 三层缓存与多源降级
- [回测流程](workflows/backtest.md) — T+1 / PIT 回测引擎
- [策略体系](domain/strategies.md) — 策略语义簇与综合评分
- [运维与部署](operations/runbook.md) — 缓存、监控、故障恢复、部署脚本
- [数据源与 API](integrations/data-sources.md) — 多源适配、REST/SSE 接口
- [测试指南](testing/guide.md) — 回测与单元测试运行方式
- [源码地图](source-map.md) — 目录结构与各文件职责
