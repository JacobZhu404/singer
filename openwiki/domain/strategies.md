---
type: domain
title: 策略体系
description: 策略语义簇、综合评分、风险过滤
tags: [stock-screener, domain, strategy]
---

# 策略体系

## 策略全景

| ID | 中文名 | 分组 | 核心逻辑 | 基准胜率 | 权重 | 标签 |
|----|--------|------|---------|---------|------|------|
| `macd_bull` | MACD 多头排列 | G1 趋势顺势 | DIF/DEA 零轴上金叉 + 四线多头 | ~0.46 | 0.91 | 趋势/中线 |
| `right_side` | 右侧交易 | G1 趋势顺势 | 突破关键阻力 + MA金叉 + MA60 + RSI 健康 | — | 1.05 | 突破/中线 |
| `strong_stock` | 强势股选股 | G1 趋势顺势 | 放量 + 红肥绿瘦 + MACD 零轴 | — | 0.92 | 强势/短线 |
| `golden_cross` | 均线金叉 | G1 趋势顺势 | 三线多头 + RSI（比 macd_bull 更早入场） | — | 0.79 | 金叉/均线 |
| `tail_market` | 尾盘强势 | G1 趋势顺势 | 温和涨幅 + 量能 + 均线多头 + 收盘新高 | — | 1.17 | 尾盘/短线 |
| `momentum` | 横截面动量 | G1 趋势顺势 | Z-score 波动率标准化动量 + 趋势一致性 | — | 0.92 | 动量/风险调整 |
| `td_sequential` | 神奇九转 | G2 反转超跌 | TD Sequential 买入九转 | — | 0.99 | 反转/短线 |
| `rsi_oversold` | RSI 超卖 | G2 反转超跌 | RSI<30 + 回升 + 跌破 MA20 | — | 0.94 | 超卖/反弹 |
| `bollinger_lower_bounce` | 布林下轨反弹 | G2 反转超跌 | 触及下轨 + 缩量止跌 | — | 0.75 | 布林带/均值回归 |
| `reversal` | 横截面反转 | G2 反转超跌 | 全市场近5日跌幅排名 + 当日企稳 | — | 0.95 | 反转/横截面 |
| `volume_breakout` | 量价突破 | G3 突破创新高 | 量比>2 + 突破30日高点 + 回踩确认 | ~0.45 | 1.02 | 突破/放量 |
| `bollinger_breakout` | 布林收口突破 | G3 突破创新高 | 10日最窄带宽 + 放量突破中轨 | — | 0.95 | 布林带/波动率 |
| `rps_breakout` | RPS 相对强度突破 | G3 突破创新高 | 欧奈尔多周期 RPS 加权 + 创新高 | — | 1.39 | 欧奈尔/领涨 |
| `high_tight_flag` | 高紧旗形 | G3 突破创新高 | 旗杆暴涨 + 高位窄幅缩量整理 | — | 1.02 | 欧奈尔/旗形 |
| `limit_up_gene` | 涨停基因 v2 | G3 突破创新高 | 真封板(close==high) + 回撤甜区 + 量价拐头 | — | 0.92 | 涨停/回踩 |
| `chanlun_strict` | 缠论严格版 | G4 形态独立 | 分型→笔→中枢→背驰→三类买点 | — | 0.65 | 缠论/独立 |

> 注：实际共 16 个策略，与 `strategies/` 目录及 `registry.py` 的 G1(6)+G2(4)+G3(5)+G4(1) 自洽一致。

## 策略注册表 `STRATEGY_REGISTRY`

策略按**语义簇**分组注册，每组内含权重配置：

| 分组 | ID 数 | 策略 |
|------|-------|------|
| G1 趋势顺势 | 6 | macd_bull, right_side, strong_stock, golden_cross, tail_market, momentum |
| G2 反转超跌 | 4 | td_sequential, rsi_oversold, bollinger_lower_bounce, reversal |
| G3 突破·创新高 | 5 | volume_breakout, bollinger_breakout, rps_breakout, high_tight_flag, limit_up_gene |
| G4 形态独立 | 1 | chanlun_strict（Jaccard < 8%，最独立） |

每个注册项包含：`cls`, `name`, `description`, `tags`, `icon`, `weight`, `group`。

## 策略互斥与互补

- **同组衰减**：G1 内多策略同时命中同一股票时，最高分者全额计分，其余 × 0.3
- **跨组共识**：不同组的策略同时命中，获得额外共识加成（每多一个命中策略 +5%，上限 15%）
- **Jaccard 独立性**：G4 缠论与其他全部策略的 Jaccard 交集 < 8%，是最独立的策略

## 策略扩展指南

```python
# strategies/my_strategy.py
from .base import BaseStrategy, StockSignal

class MyStrategy(BaseStrategy):
    name = "my_strategy"
    description = "我的自定义策略"
    base_win_rate = 0.55

    def _evaluate_single_stock(self, code, scanner, name_map, trade_date):
        indicators = scanner.get_indicators(code, days=120)
        if not indicators or len(indicators["kline"]) < 60:
            raise self._SkipStock()
        # ... 评分逻辑 ...
        if score >= 50:
            return StockSignal(ts_code=code, name=name_map.get(code, ""),
                               strategy=self.name, score=score,
                               win_rate=self.base_win_rate, signals=signals, ...)
        return None
```

然后在 `registry.py` 中注册。

## 相关页面

- [架构概览](architecture/overview.md) — 策略层（BaseStrategy 模板方法）
- [回测流程](workflows/backtest.md) — 策略如何接入回测
- [实时筛选流程](workflows/screening.md) — 线上策略调度与合并
