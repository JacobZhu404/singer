---
type: source-map
title: 源码地图
description: 仓库目录结构与各文件职责
tags: [stock-screener, source-map]
---

# 源码地图

```
stock_screener/
├── main.py                  # 主入口（Web / CLI / list 三种模式）
├── start.sh                 # 本地一键启动脚本
├── Dockerfile               # Python 3.11-slim 镜像（Gunicorn 生产）
├── docker-compose.yml       # 通用 Docker Compose 配置
├── requirements.txt         # Python 依赖清单
│
├── strategies/              # 策略层（注册策略）
│   ├── base.py              # BaseStrategy + StockSignal + ScreenResult
│   ├── registry.py          # STRATEGY_REGISTRY + get_strategy() + list_strategies()
│   ├── macd_bull.py         # MACD 多头排列
│   ├── strong_stock.py      # 强势股选股
│   ├── td_sequential.py     # 神奇九转
│   ├── right_side.py        # 右侧交易
│   ├── rsi_oversold.py      # RSI 超卖
│   ├── bollinger_lower_bounce.py  # 布林下轨反弹
│   ├── bollinger_breakout.py        # 布林收口突破
│   ├── volume_breakout.py   # 量价突破
│   ├── chanlun_strict.py    # 缠论严格版（分型/笔/中枢/背驰/三类买点）
│   ├── momentum.py          # 横截面动量
│   ├── golden_cross.py      # 均线金叉（宽松版）
│   ├── rps_breakout.py      # 欧奈尔 RPS 相对强度突破
│   ├── high_tight_flag.py   # 欧奈尔高紧旗形
│   ├── tail_market.py       # 尾盘强势
│   ├── reversal.py          # 横截面反转
│   └── limit_up_gene.py     # 涨停基因 v2
│
├── core/                    # 引擎层
│   ├── engine.py            # ScreenEngine（调度+合并+推荐+进度管理）
│   ├── server.py            # Flask Web 服务 + RESTful API + SSE
│   ├── observability.py     # Observability 全局事件收集器
│   ├── constants.py         # 集中配置常量（并发/阈值/回测参数）
│   └── risk_scanner.py      # 轻量风险/卖出/买入风险扫描
│
├── data/                    # 数据层
│   ├── fetcher.py           # MarketScanner + get_stock_history/list/realtime
│   ├── local_cache.py       # CSV 文件系统缓存（线程安全）
│   ├── data_sources.py      # 多数据源适配（新浪/腾讯/东财）
│   ├── data_layer.py        # 6 层智能数据更新决策
│   ├── baostock_source.py   # Baostock 收盘数据源
│   ├── tencent_realtime.py  # 腾讯实时分时
│   ├── tencent_batch.py     # 腾讯批量实时（90只/HTTP）
│   ├── realtime_merge.py    # 实时行情合并到日线
│   ├── fundamentals.py      # 基本面（PE/PB/市值）
│   ├── tdx_offline.py       # 通达信 .day 离线兜底
│   ├── import_tdx.py        # TDX 数据导入工具
│   ├── market_calendar.py   # A 股交易日历（节假日感知）
│   └── cache/               # 缓存目录
│       ├── klines/          # {code6}.csv K线数据
│       ├── stocks.json      # 股票列表缓存
│       ├── meta.json        # 缓存元数据
│       ├── trade_calendar.json  # Baostock 校正后的交易日历
│       └── delisted_blocklist.json  # 失效股黑名单（TTL 90天）
│
├── backtest/                # 回测框架
│   ├── backtest_engine.py   # 完整回测引擎（进程并行 + T+1 + PIT）
│   ├── backtest_quick.py    # 快速测试脚本（少量股票/策略/日期）
│   ├── pit_scanner.py       # PointInTimeScanner（时点扫描器）
│   └── results/             # 回测结果存储
│
├── portfolio/               # 持仓管理
│   ├── manager.py           # Portfolio（买入/卖出/盈亏追踪/持久化）
│   └── sell_analyzer.py     # SellAnalyzer（多维度卖出信号检测）
│
├── tools/                   # 离线运维/研究工具
│   ├── factor_portfolio_backtest.py  # 含成本因子组合回测
│   ├── ic_validation.py              # IC/IR 验证
│   ├── import_tdx_to_cache.py        # TDX .day → cache CSV
│   ├── composite_backtest.py        # 全策略组合回测
│   ├── gtja_alpha191.py              # Alpha191 因子实现
│   ├── chanlun_param_sweep.py        # 缠论参数扫描
│   └── ...
│
├── utils/                   # 工具层
│   ├── indicators.py        # 技术指标库（pandas 自实现，无 TA-Lib）
│   ├── sell_signals.py      # 卖出信号检测工具
│   ├── cross_section.py     # 横截面因子计算
│   ├── market_trend.py      # 大盘趋势判断
│   ├── fundamental_flags.py # 基本面因子标志
│   └── precalc.py           # 指标预计算
│
└── web/                     # 前端
    ├── templates/index.html # 深色主题 + SSE 实时推送 + 诊断面板
    └── static/              # 静态资源
```

## 相关页面

- [架构概览](architecture/overview.md)
- [快速开始](quickstart.md)
- [策略体系](domain/strategies.md)
