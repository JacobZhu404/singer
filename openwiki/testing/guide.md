---
type: testing
title: 测试指南
description: 回测与单元测试的运行方式
tags: [stock-screener, testing]
---

# 测试指南

## 依赖与运行

```bash
# 首次：创建并安装依赖（纯 Python 依赖，无需 brew/TA-Lib）
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt

# 之后所有命令都走 .venv/bin/python
.venv/bin/python -m pytest tests/ -q
```

> 指标全部基于 pandas/numpy 自实现（见 `utils/indicators.py`），**不依赖 TA-Lib**。

## 回测运行

```bash
# 快速测试（少量股票/策略/日期）
.venv/bin/python backtest/backtest_quick.py

# 完整回测
.venv/bin/python backtest/backtest_engine.py

# 组合回测
.venv/bin/python tools/composite_backtest.py
```

回测引擎设计要点见 [回测流程](workflows/backtest.md)（T+1 入场、Point-In-Time 防未来函数、Checkpoint 续跑、成本模型）。

## 验证类工具

| 工具 | 用途 |
|------|------|
| `tools/ic_validation.py` | IC/IR 因子有效性验证 |
| `tools/factor_portfolio_backtest.py` | 含成本因子的组合回测 |
| `tools/gtja_factor_validation.py` | GTJA Alpha191 因子验证 |
| `tools/chanlun_param_sweep.py` | 缠论参数扫描 |

## 相关页面

- [回测流程](workflows/backtest.md)
- [架构概览](architecture/overview.md)
- [策略体系](domain/strategies.md)
