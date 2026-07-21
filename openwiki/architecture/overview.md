---
type: architecture
title: 架构概览
description: 三层架构、核心模块（策略层/引擎层）、关键设计原则
tags: [stock-screener, architecture]
---

# 架构概览

## 关键设计原则

| 原则 | 说明 |
|------|------|
| **模板方法模式** | 子类只实现 `_evaluate_single_stock()`，基类封装并发/兜底/计数 |
| **三层缓存** | 内存(LRU, 6500上限) → 本地CSV → 网络API，逐级降级 |
| **多源冗余** | 新浪/腾讯/东方财富/Baostock 四层数据源，自动降级 |
| **T+1 回测** | 入场建模为信号日次日开盘价，杜绝未来函数 |
| **PIT 扫描** | Point-In-Time Scanner 确保回测时点严格不泄露未来信息 |
| **可观测性** | 环形缓冲(1000条) + 阶段耗时 + 错误计数，不阻塞主流程 |

## 核心模块

### 策略层（Strategies）

采用**模板方法模式**，子类只需实现 `_evaluate_single_stock()`。

```python
class BaseStrategy(ABC):
    name: str = "base"
    description: str = ""
    base_win_rate: float = 0.5

    @abstractmethod
    def _evaluate_single_stock(self, code, scanner, name_map, trade_date) -> Optional[StockSignal]:
        """评估单只股票，返回 StockSignal 或 None"""
        pass

    def screen(self, stock_list, scanner=None, max_workers=20, stop_event=None) -> ScreenResult:
        """通用并行筛选模板（内部 20 线程 ThreadPoolExecutor）"""
```

- `StockSignal` dataclass 封装信号详情（评分、胜率、触发信号、风险标签）
- `_SkipStock` 内部异常：数据不足时抛出，该股票不计入 scanned 计数
- `prepare_for_date()` hook：横截面策略（如 reversal）的全市场预处理入口
- `set_progress_callback()`：进度回调支持

策略按**语义簇**分组注册（详见 [策略体系](domain/strategies.md)）。

### 引擎层（Core）

#### `ScreenEngine` — 选股引擎

核心职责：

- **策略调度**：串行执行各策略（策略内部已用线程池并行）
- **结果合并**：`merge_results()` 加权评分，同组去重（INTRA_GROUP_DECAY = 0.3）
- **综合推荐**：多策略共识加成 → 综合评分 → Top-N 推荐
- **进度管理**：线程安全的进度状态，支持 SSE 实时推送
- **停止控制**：`threading.Event` 中断机制

```python
class ScreenEngine:
    def __init__(self, market="主板", top_n=20):
        self.market = market  # 主板/创业板/科创板/all
        self.top_n = top_n

    def get_recommendation(self, strategies=None, top_n=20,
                           progress_callback=None, force_refresh=False,
                           on_strategy_done=None, skip_download=False) -> dict:
        """主入口：获取综合推荐"""
```

**综合评分模型：**

```
综合评分 = weighted_score × 0.50 + rank_bonus × 0.20 + consensus_bonus × 0.05
                    + risk_adjustment × 0.20 + market_strength × 0.03

其中：
- weighted_score = Σ(strategy_score × weight × win_factor)
- win_factor = 0.6 + 0.8 × base_win_rate
- 同组衰减：同组多策略命中时，最高者全额，其余 × 0.3
- 市场调节：牛市门槛 × 0.9，熊市门槛 × 1.3
```

**胜率计算模型：**

```
综合预期胜率 = 基础胜率
  + (score - 50) / 100 × 20%          # 评分加成
  + min(信号数 × 3%, 15%)             # 信号叠加加成
  + min((命中策略数-1) × 5%, 15%)     # 多策略共识加成
  上限 90%
```

#### `MarketScanner` — 数据扫描器

三层缓存架构：

1. **内存缓存**：`_kline_cache` dict，LRU 淘汰（上限 6500 只）
2. **本地 CSV**：`data/cache/klines/{code6}.csv`
3. **网络 API**：新浪/腾讯/东方财富/Baostock

```python
class MarketScanner:
    def get_history(self, code, days=60) -> pd.DataFrame:
        """内存 → 本地CSV → 网络，逐级降级"""

    def prefetch_batch(self, codes, days=60, max_workers=50, stop_event=None) -> dict:
        """并发预加载 K 线到内存，带 3 轮补全 + 降级机制"""

    def get_indicators(self, code, days=60) -> dict:
        """获取预计算指标包（MACD/MA/RSI/布林带/量比等）"""
```

**预加载策略（多轮并发 + 串行兜底）：**

- 第 0 轮：批量实时快速路径（腾讯 90只/HTTP，补全只缺今日一行的标的）
- 第 1~4 轮：高并发逐级降级（50 → 20 → 10 → 5 workers）
- 最终：串行兜底（1 worker，间隔 1s 防限流）

#### `Observability` — 可观测性收集器

线程安全的环形缓冲（默认 1000 条事件），记录错误/警告/调试事件、阶段耗时（`obs.timer()` 上下文管理器）、按 level/source 的计数器。

```python
obs.error("data.fetch", "fetch_one", "failed: timeout", context={"code": "600519"})
with obs.timer("stage", "prefetch_klines", context={"total": 317}):
    ...  # 自动记录耗时
summary = obs.summary()  # 事件摘要 + 慢操作 Top5
```

#### 风险扫描（`core/risk_scanner.py`）

轻量风险/卖出/买入风险评估，仅读取已缓存 K 线数据（模块级函数，非类）：

```python
def quick_risk_scan(code, df=None) -> (tag, reasons, score, flags)
    # tag ∈ {"safe", "watch", "conflict", "high_risk", "unknown"}

def quick_sell_scan(code, df=None) -> dict
    # 检测 MACD 死叉/MA 空头排列/RSI 超买/布林上轨/TD 卖出计数

def assess_buy_risk(df) -> dict   # 定义在 utils/sell_signals.py，被 risk_scanner 调用
    # 买入风险评估：卖出信号强 = 买入风险高
```

#### 集中配置 `Constants`

| 类别 | 常量 | 默认值 | 说明 |
|------|------|--------|------|
| 并发 | `MAX_WORKERS_STRATEGY` | 20 | 策略并行线程数 |
| | `MAX_WORKERS_PREFETCH` | 50 | 数据预取并发数 |
| | `MAX_WORKERS_PRECALC` | 16 | 指标预计算并发数 |
| K线 | `DEFAULT_KLINE_DAYS` | 60 | 默认 K 线天数 |
| | `PREFETCH_KLINE_DAYS` | 120 | 预取 K 线天数 |
| | `SCREEN_MAX_STALE_DAYS` | 15 | 实时筛选 stale 上限 |
| 评分 | `MIN_SINGLE_SCORE` | 20 | 单策略原始分门槛 |
| | `MIN_WEIGHTED_SCORE` | 30 | 加权总分门槛 |
| | `INTRA_GROUP_DECAY` | 0.3 | 同组衰减系数 |
| | `WIN_FACTOR_BASE` | 0.6 | 胜率因子基准 |
| | `WIN_FACTOR_SLOPE` | 0.8 | 胜率因子斜率 |
| 市场 | `BEAR_FACTOR` | 1.3 | 熊市门槛系数 |
| | `BULL_FACTOR` | 0.9 | 牛市门槛系数 |
| 回测 | `HOLD_PERIODS` | [2,5,10,30] | 持有期（天） |
| | `BACKTEST_SLIPPAGE_PCT` | 0.02% | 单边滑点 |
| | `BACKTEST_COMMISSION_PCT` | 0.00854% | 单边佣金 |
| | `BACKTEST_STAMP_DUTY_PCT` | 0.05% | 印花税（仅卖出） |
| | `BACKTEST_BENCHMARK_CODE` | 000852 | 基准指数（中证1000） |

## 相关页面

- [数据源与 API](integrations/data-sources.md) — 数据层、指标库、REST/SSE 接口
- [运维与部署](operations/runbook.md) — 持仓管理、缓存与部署
- [策略体系](domain/strategies.md) — 策略层细节
