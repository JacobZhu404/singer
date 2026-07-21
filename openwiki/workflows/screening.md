---
type: workflows
title: 实时筛选流程
description: 一次筛选从用户触发到返回综合推荐的完整链路
tags: [stock-screener, workflow, screening]
---

# 实时筛选流程

```
用户触发筛选（Web POST /api/screen 或 CLI screen）
    │
    ▼
ScreenEngine.get_recommendation()
    │
    ├─► 1. 获取股票列表（get_stock_list）
    │     └─► 缓存有效？→ 用缓存；否则 → 网络 API → 写缓存
    │     └─► 按市场过滤（主板/创业板/科创板）
    │     └─► 剔除非可交易代码（北交所优先股 82xxxx, 老三板 400xxx/420xxx）
    │     └─► 剔除失效股黑名单
    │
    ├─► 2. 数据预加载（MarketScanner.prefetch_batch）
    │     ├─► 第0轮：批量实时快速路径（腾讯 90只/HTTP）
    │     ├─► 第1-4轮：并发降级（50→20→10→5 workers）
    │     └─► 最终：串行兜底（1 worker, 1s间隔）
    │     └─► 失败股票自动登记到失效股黑名单
    │
    ├─► 3. 指标预计算（precalc）
    │     └─► 多线程计算 MACD/MA/RSI/布林带/量比等
    │
    ├─► 4. 策略执行（串行调度，策略内部并行）
    │     ├─► Strategy 1.screen() → ThreadPoolExecutor(20 workers)
    │     │     └─► 每只股票 → _evaluate_single_stock() → StockSignal
    │     │     └─► SSE 推送策略完成事件
    │     ├─► Strategy 2.screen()
    │     └─► ...
    │
    ├─► 5. 结果合并（merge_results）
    │     ├─► 加权评分：Σ(score × weight × win_factor)
    │     ├─► 排名加成：按策略内排名给 bonus
    │     ├─► 共识加成：命中策略数越多加分越多
    │     ├─► 同组衰减：同组多策略命中 × 0.3
    │     ├─► 市场调节：牛/熊市门槛调整
    │     └─► 综合评分 = weighted×50% + rank×20% + consensus×5% + risk×20% + market×3%
    │
    ├─► 6. 风险扫描（quick_risk_scan + quick_sell_scan + assess_buy_risk）
    │     └─► 为每只推荐股票附加风险标签/卖出信号/买入风险
    │
    └─► 7. 返回综合推荐（Top-N）
          └─► 含：综合评分/命中策略/信号描述/风险标签/卖出建议
```

## 持仓管理数据流（嵌入筛选）

```
用户点击"买入"
    │
    ▼
Portfolio.buy(code, price, shares, ...)
    ├─► 扣除现金
    ├─► 添加到 positions
    ├─► 记录到 trades
    └─► 持久化到 portfolio.json
    │
    ▼
每次筛选时自动评估持仓
    ├─► 获取持仓股最新价格
    ├─► 计算盈亏（浮盈/已实现盈亏）
    ├─► 对每只持仓运行 SellAnalyzer
    │     └─► RSI/MACD/布林带/均线/风险标志 → HOLD/WATCH/REDUCE/SELL/URGENT
    └─► 返回持仓概览 + 卖出建议
```

## 相关页面

- [数据获取流程](data-fetch.md) — 三层缓存与多源降级细节
- [回测流程](backtest.md) — 离线回测引擎
- [运维与部署](operations/runbook.md) — 持仓持久化与故障恢复
