---
type: operations
title: 运维与部署
description: 持仓管理、缓存、监控、故障恢复与部署脚本
tags: [stock-screener, operations, deployment]
---

# 运维与部署

## 持仓管理（Portfolio）

### `Portfolio` — 虚拟持仓

```python
class Portfolio:
    def __init__(self, initial_cash=1_000_000.0):
        # 初始资金 100 万

    def buy(self, code, name, price, shares, date, score=0, strategy=""):
        """虚拟买入"""

    def sell(self, code, price, date, reason=""):
        """虚拟卖出"""

    def get_account(self, price_map) -> dict:
        """计算账户状态：持仓市值/现金/总盈亏/当日涨跌"""

    def get_trades(self) -> list:
        """历史交易记录"""
```

持久化到 `data/portfolio.json`。

### `SellAnalyzer` — 卖出信号分析器

五维分析 → 五级建议：

| 维度 | 信号 | 权重 |
|------|------|------|
| 止盈 | RSI 超买、触及布林上轨、连续高位滞涨 | 15-25 |
| 止损 | 跌幅>10%/5%/跌破MA5/MA20 | 8-40 |
| 警示 | 量价背离、MACD 顶背离、TD 九转卖出 | 10-20 |
| 趋势 | MA 空头排列、跌破关键支撑 | 10-20 |
| 风险 | 跌停/巨量/乌云盖顶/高位十字星 | 12-25 |

五级建议：`HOLD` → `WATCH` → `REDUCE` → `SELL` → `URGENT`

## 缓存管理

| 缓存位置 | 内容 | 说明 |
|----------|------|------|
| `data/cache/klines/*.csv` | K 线数据 | 每只股票一个文件 |
| `data/cache/stocks.json` | 股票列表 | 全市场 A 股 |
| `data/cache/meta.json` | 缓存元数据 | 更新时间/条数 |
| `data/cache/trade_calendar.json` | 交易日历校正 | Baostock 校正结果 |
| `data/cache/delisted_blocklist.json` | 失效股黑名单 | TTL 90 天 |
| `data/last_screen_result.json` | 上次筛选结果 | 启动时恢复 |
| `data/portfolio.json` | 虚拟持仓 | 持久化持仓数据 |

## 监控与诊断

通过 `Observability` 收集器可查询：

- `obs.recent(n=200)` — 最近 N 条事件
- `obs.counters()` — 按 level/source 的计数
- `obs.summary()` — 摘要（事件总数/错误分布/慢操作 Top5）

## 故障恢复

| 场景 | 恢复机制 |
|------|---------|
| 服务重启 | 自动恢复上次筛选结果 + 持仓数据 |
| 网络不可用 | 降级到本地 CSV 缓存 / 通达信离线 |
| 单数据源失败 | 自动切换到备用数据源（新浪→腾讯→东财→Baostock） |
| 并发全部失败 | 逐步降级并发数（50→20→10→5→串行） |
| 失效股票 | 自动登记黑名单，90 天后重探 |
| 筛选中断 | 15 分钟超时保护，强制重置 |

## 部署脚本

| 脚本 | 用途 |
|------|------|
| `deploy-alibaba.sh` | 阿里云 ECS 一键部署 |
| `deploy-tencent.sh` | 腾讯云 CVM 一键部署 |
| `start.sh` | 本地一键启动 |
| `Dockerfile` | 容器镜像构建 |
| `docker-compose.yml` | 通用 Docker Compose |
| `docker-compose.alibaba.yml` | 阿里云覆盖配置 |
| `docker-compose.tencent.yml` | 腾讯云覆盖配置 |

Docker 镜像基于 `python:3.11-slim`，生产使用 Gunicorn（2 workers × 4 threads）。

## 相关页面

- [实时筛选流程](workflows/screening.md) — 持仓数据流
- [数据源与 API](integrations/data-sources.md) — 缓存与数据层
- [测试指南](testing/guide.md) — 回测运行
