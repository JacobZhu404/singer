---
type: integrations
title: 数据源与 API
description: 多源数据适配、技术指标库、REST/SSE/CLI 接口
tags: [stock-screener, integrations, api]
---

# 数据源与 API

## 数据层（Data）

### 多数据源适配 `DataSources`

```
┌──────────────────────────────────────────────────────────┐
│                   DataSources 层级                        │
│                                                          │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────┐  │
│  │   新浪财经    │  │   腾讯财经    │  │  东方财富      │  │
│  │  finance.sina │  │  qt.gtimg.cn │  │  quote.east.. │  │
│  └──────┬───────┘  └──────┬───────┘  └───────┬───────┘  │
│         └─────────────────┼──────────────────┘          │
│                           ▼                             │
│                  ┌──────────────┐                       │
│                  │  DataMgr     │  ← 自动降级           │
│                  │  .get_kline()│                       │
│                  └──────┬───────┘                       │
│                         ▼                               │
│  ┌───────────────────────────────────────────┐         │
│  │          本地缓存 (CSV + JSON)             │         │
│  └───────────────────────────────────────────┘         │
│                         ▲                               │
│  ┌───────────────────────────────────────────┐         │
│  │      Baostock (收盘数据写入本地)            │         │
│  └───────────────────────────────────────────┘         │
└──────────────────────────────────────────────────────────┘
```

### 数据更新决策 `DataLayer`（6 层设计）

1. 判断数据是否需要更新
2. 需要更新实时还是收盘数据
3. 实时→新浪/腾讯，收盘→Baostock
4. Baostock 写入本地，合并到历史数据
5. 交易时间→实时数据，非交易时间→收盘数据
6. 通达信离线 `.day` 兜底

### 交易日历 `MarketCalendar`

- 硬编码官方休假日（2025-2026）
- Baostock 校正缓存（`trade_calendar.json`，只增不减）
- 统一来源：`is_market_open()`, `is_holiday()`, `get_last_trading_date()`

### 失效股黑名单 `DelistedRegistry`

- 自动登记「无历史 + 全源失败」的死码
- TTL 90 天，到期自动重探
- 区分停牌与退市，避免误杀复牌股票

## 技术指标库（Utils）

全部基于 `pandas`/`numpy` 自实现，**不依赖 TA-Lib**：

| 函数 | 说明 |
|------|------|
| `calc_macd(close)` | DIF, DEA, MACD柱 |
| `calc_rsi(close, period=14)` | RSI |
| `calc_bollinger(close, period=20)` | 上轨/中轨/下轨 |
| `calc_ma(close, periods=[5,10,20,60])` | 多周期均线 |
| `calc_volume_ratio(vol, period=5)` | 量比 |
| `td_sequential_count(close)` | TD Sequential 计数 |
| `calc_risk_flags(close, high, low, vol, pct_chg)` | 风险标志（跌停/巨量/乌云盖顶等） |
| `detect_sell_signals(df)` | 卖出信号检测 |
| `assess_buy_risk(code, df)` | 买入风险评估 |
| `compute_indicator_bundle(df)` | 批量计算所有指标 |
| `get_limit_pct(code, name)` | 根据代码/名称返回涨跌停阈值 |

## REST API

| 接口 | 方法 | 说明 | 请求体 |
|------|------|------|--------|
| `/` | GET | Web UI 页面 | — |
| `/api/strategies` | GET | 获取策略列表 | — |
| `/api/screen` | POST | 异步启动筛选 | `{strategies, market, top_n, force_refresh, skip_download}` |
| `/api/screen/progress` | GET | 获取筛选进度 | — |
| `/api/screen/stop` | POST | 停止筛选 | — |
| `/api/result` | GET | 获取筛选结果 | — |
| `/api/status` | GET | 系统状态 | — |
| `/api/portfolio` | GET | 持仓概览 | — |
| `/api/portfolio/buy` | POST | 虚拟买入 | `{code, name, price, shares}` |
| `/api/portfolio/sell` | POST | 虚拟卖出 | `{code, price}` |
| `/api/portfolio/trades` | GET | 交易历史 | — |
| `/api/quick_screen` | POST | 同步单策略筛选 | `{strategy, market, top_n}` |
| `/api/data_status` | GET | 数据缓存状态 | — |

## SSE 事件流

筛选过程中通过 SSE 推送以下事件：

| 事件 | 数据 |
|------|------|
| `progress` | `{phase, current, pct, strategies: {name: {status, pct, hits, scanned}}}` |
| `strategy_done` | `{name, data: {strategy_name, hit_count, top_stocks}}` |
| `done` | `{comprehensive_picks, strategy_details}` |
| `error` | `{error: string}` |

## CLI 命令

```bash
python main.py list                                      # 列出所有策略
python main.py screen                                    # 运行所有策略（默认主板 Top10）
python main.py screen -s macd_bull strong_stock          # 运行指定策略
python main.py screen -m 创业板 -n 5                     # 指定市场 + 数量
python main.py screen -s macd_bull --json                # JSON 输出
python main.py web --port 5188 --debug                  # Web 模式
```

## 相关页面

- [数据获取流程](workflows/data-fetch.md) — 三层缓存与降级
- [架构概览](architecture/overview.md) — 引擎层与 Constants
- [运维与部署](operations/runbook.md) — 缓存目录与持久化
