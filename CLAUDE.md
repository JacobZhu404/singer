# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

「歌者」- 智能A股选股系统，基于同花顺/东方财富数据的全自动股票筛选工具，支持多策略并行扫描、综合评分推荐。

## 环境与启动（重要）

**运行时是 Python 3.11，必须用项目内 venv：`.venv/bin/python`。**
系统默认 `python3` 是 3.7.5，缺 flask/akshare/talib 且 pandas 过旧，**用它启动必然失败**——
任何人/任何大模型都不要用裸 `python3` 跑本项目。

```bash
# 首次：创建并安装依赖
python3.11 -m venv .venv
TA_INCLUDE_PATH="$(brew --prefix ta-lib)/include" \
TA_LIBRARY_PATH="$(brew --prefix ta-lib)/lib" \
  .venv/bin/python -m pip install -r requirements.txt

# 之后所有命令都走 .venv/bin/python（或先 source .venv/bin/activate）
.venv/bin/python -m pytest tests/ -q
```

## 常用命令

```bash
# 启动Web服务
.venv/bin/python main.py web [--port 5188]

# 命令行筛选
.venv/bin/python main.py screen -s macd_bull strong_stock -m 主板 -n 10

# 列出所有策略
.venv/bin/python main.py list

# 输出JSON格式
.venv/bin/python main.py screen -s macd_bull --json

# 回测快速测试
.venv/bin/python backtest/backtest_quick.py
```

## 架构概览

```
三层架构:
├── strategies/        # 策略层 (10+策略)
│   ├── base.py       # BaseStrategy模板方法模式
│   └── registry.py   # 策略注册表
│
├── core/             # 引擎层
│   ├── engine.py     # ScreenEngine: 调度+合并+推荐
│   ├── server.py    # Flask Web服务
│   └── constants.py # 集中配置常量
│
├── data/            # 数据层
│   ├── fetcher.py   # MarketScanner (三层缓存: 内存→文件→网络)
│   └── local_cache.py
└── portfolio/       # 持仓管理
```

### 数据流

1. `ScreenEngine.get_recommendation()` 调用 `download_data()` 预加载K线到内存
2. 串行运行各策略（策略内部已用ThreadPoolExecutor并行）
3. `merge_results()` 合并多策略结果，加权评分
4. 返回综合推荐列表

### 关键类

- `ScreenEngine`: 主引擎，负责流程调度
- `MarketScanner`: 数据扫描器，三层缓存+LUR淘汰
- `BaseStrategy`: 策略基类，子类只需实现 `_evaluate_single_stock()`

## 开发注意事项

### 添加新策略

```python
# strategies/new_strategy.py
from .base import BaseStrategy, StockSignal

class NewStrategy(BaseStrategy):
    name = "new_strategy"
    description = "新策略描述"
    base_win_rate = 0.55

    def _evaluate_single_stock(self, code, scanner, name_map, trade_date):
        # 评分逻辑
        if score >= 50:
            return StockSignal(...)
        return None
```

然后在 `registry.py` 注册。

### 配置管理

- 硬编码配置应放入 `core/constants.py`
- 参考已有常量: `MAX_WORKERS_*`, `DEFAULT_KLINE_DAYS`, `MIN_SINGLE_SCORE` 等

### 线程安全

- `MarketScanner._kline_cache` 访问需要 `self._lock` 保护
- `engine.py` 中 `prefetch_merge` 已加锁

### 已知问题

- `stocks.json` 使用 `"ts_code"/"name"` 而非旧 `"代码"/"名称"`
- 网络API有限流，需适当降级并发