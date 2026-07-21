---
type: workflows
title: 数据获取流程
description: MarketScanner 的三层缓存与多源自动降级链路
tags: [stock-screener, workflow, data]
---

# 数据获取流程

```
MarketScanner.get_history(code, days=60)
    │
    ├─► 1. 内存缓存命中？→ 返回
    │
    ├─► 2. 本地 CSV 存在且条数足够？→ 加载 → 写入内存 → 返回
    │
    └─► 3. 网络获取（get_stock_history）
          │
          ├─► 3a. 通达信离线 .day（最新 ≤ 3天）
          │
          ├─► 3b. DataLayer 智能判断
          │     ├─► 需要实时？→ 新浪/腾讯
          │     └─► 需要收盘？→ Baostock → 写本地 CSV
          │
          └─► 3c. 兜底：本地 CSV 旧缓存
```

## 三层缓存

1. **内存缓存**：`_kline_cache` dict，LRU 淘汰（上限 6500 只）
2. **本地 CSV**：`data/cache/klines/{code6}.csv`
3. **网络 API**：新浪 / 腾讯 / 东方财富 / Baostock

## 多源降级

- 实时数据：新浪 → 腾讯 → 东方财富
- 收盘数据：Baostock 写入本地，合并到历史
- 极端兜底：通达信离线 `.day` 文件
- 并发全失败：逐级降级并发数（50 → 20 → 10 → 5 → 串行）

## 相关页面

- [实时筛选流程](screening.md)
- [数据源与 API](integrations/data-sources.md) — DataSources / DataLayer 结构
- [运维与部署](operations/runbook.md) — 缓存目录与失效股黑名单
