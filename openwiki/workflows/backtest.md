---
type: workflows
title: 回测流程
description: 基于 T+1 与 Point-In-Time 的离线回测引擎
tags: [stock-screener, workflow, backtest]
---

# 回测流程

## 回测架构

```
┌─────────────────────────────────────────────────────────────┐
│                    BacktestEngine                            │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  日期循环（从缓存中抽取交易日序列）                      │   │
│  │                                                      │   │
│  │  for trade_date in trade_dates:                      │   │
│  │    1. PointInTimeScanner.set_as_of(trade_date)       │   │
│  │       → 清空指标缓存，只暴露 ≤ trade_date 的数据        │   │
│  │    2. 对每个策略：                                     │   │
│  │       a. prepare_for_date()  → 横截面全市场排名        │   │
│  │       b. 进程池并行评估每只股票：                       │   │
│  │          strategy._evaluate_single_stock()            │   │
│  │          → 返回 StockSignal（只看 as_of 之前的数据）    │   │
│  │       c. 过滤不可成交（T+1 开盘一字涨停）               │   │
│  │    3. 计算持有期收益：                                  │   │
│  │       T+1 开盘价入场 → 持有 N 天 → close 出场          │   │
│  │       → 扣除：滑点 + 佣金 + 过户费 + 印花税             │   │
│  │       → 跌停封板顺延出场                                │   │
│  │    4. 计算基准收益（中证1000）                          │   │
│  │    5. 计算 alpha = strategy_return - benchmark_return  │   │
│  └──────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

## 关键设计

| 特性 | 实现 |
|------|------|
| **T+1 入场** | 信号日 T 收盘后买入，实际成交价 = T+1 开盘价 |
| **Point-In-Time** | `PointInTimeScanner` 严格按 as_of 切片，杜绝未来函数 |
| **进程并行** | `multiprocessing.Pool`（spawn 上下文），子进程降优先级（`os.nice`） |
| **Checkpoint 续跑** | 已完成的 (date, strategy) 结果缓存，中断后可续 |
| **成本模型** | 双边 ≈ 0.127%（滑点 0.02% × 2 + 佣金 0.00854% × 2 + 过户费 0.01% × 2 + 印花税 0.05%） |
| **跌停保护** | 出场日遇跌停封板顺延最多 5 天，仍封死则丢弃该笔交易 |
| **涨停过滤** | T+1 开盘相对 T 收盘涨幅 ≥ 限板 - 0.3% → 视为一字板，跳过 |
| **基准对比** | 中证 1000（000852），因策略偏中小盘 |

## 回测工具

| 工具 | 用途 |
|------|------|
| `backtest/backtest_engine.py` | 完整回测引擎，支持所有策略 |
| `backtest/backtest_quick.py` | 快速测试（少量股票/策略/日期） |
| `backtest/pit_scanner.py` | PIT 时点扫描器 |
| `tools/composite_backtest.py` | 全策略组合回测 |
| `tools/ic_validation.py` | IC/IR 因子有效性验证 |
| `tools/factor_portfolio_backtest.py` | 含成本因子的组合回测 |
| `tools/gtja_factor_validation.py` | GTJA Alpha191 因子验证 |
| `tools/chanlun_param_sweep.py` | 缠论参数扫描 |

## 相关页面

- [测试指南](testing/guide.md) — 如何运行回测与单元测试
- [策略体系](domain/strategies.md) — 策略如何接入回测
- [实时筛选流程](screening.md) — 线上筛选与回测的差异（T+1 / PIT）
