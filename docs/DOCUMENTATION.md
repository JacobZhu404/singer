# 歌者（Gezhe）— 智能选股系统 完整文档

> 基于同花顺/东方财富数据的全自动 A 股筛选工具，支持多策略并行扫描、综合评分推荐。
> 「歌者」以《三体》文明命名——在资本市场的噪声中，捕捉微弱的秩序信号。

---

## 目录

1. [架构概览](#1-架构概览)
2. [核心模块](#2-核心模块)
3. [数据流](#3-数据流)
4. [策略体系](#4-策略体系)
5. [回测流程](#5-回测流程)
6. [依赖与运行方式](#6-依赖与运行方式)
7. [API 参考](#7-api-参考)
8. [运维与部署](#8-运维与部署)

---

## 1. 架构概览

### 1.1 三层架构

```
┌─────────────────────────────────────────────────────────────────┐
│                        Web UI (Frontend)                         │
│              Flask templates/index.html + SSE 流式推送            │
├─────────────────────────────────────────────────────────────────┤
│                        API Layer                                 │
│  Flask server (core/server.py) — RESTful API + SSE              │
├─────────────────────────────────────────────────────────────────┤
│                       Engine Layer                               │
│  ScreenEngine  ── 策略调度 / 结果合并 / 综合评分 / 风险扫描       │
│  Observability ── 全局事件/错误/耗时收集器                       │
│  RiskScanner ── 轻量风险/卖出/买入风险评估                       │
│  Constants ── 集中配置常量（阈值/并发/回测参数）                   │
├─────────────────────────────────────────────────────────────────┤
│                      Strategy Layer                              │
│  BaseStrategy (模板方法) → 16 个具体策略                          │
│  Registry ── 策略注册表（含权重/分组/评分）                       │
├─────────────────────────────────────────────────────────────────┤
│                       Data Layer                                 │
│  MarketScanner ── 三层缓存（内存 → 本地 CSV → 网络）              │
│  DataSources ── 多源适配（新浪/腾讯/东方财富/Baostock）            │
│  DataLayer ── 6 层智能更新决策                                    │
│  LocalCache  ── CSV 文件系统缓存                                 │
│  TDX Offline ── 通达信 .day 离线兜底                              │
│  MarketCalendar ── A 股交易时间/节假日日历                       │
└─────────────────────────────────────────────────────────────────┘
```

### 1.2 关键设计原则

| 原则 | 说明 |
|------|------|
| **模板方法模式** | 子类只实现 `_evaluate_single_stock()`，基类封装并发/兜底/计数 |
| **三层缓存** | 内存(LRU, 6500上限) → 本地CSV → 网络API，逐级降级 |
| **多源冗余** | 新浪/腾讯/东方财富/Baostock 四层数据源，自动降级 |
| **T+1 回测** | 入场建模为信号日次日开盘价，杜绝未来函数 |
| **PIT 扫描** | Point-In-Time Scanner 确保回测时点严格不泄露未来信息 |
| **可观测性** | 环形缓冲(1000条) + 阶段耗时 + 错误计数，不阻塞主流程 |

### 1.3 目录结构

```
stock_screener/
├── main.py                  # 主入口（Web / CLI / list 三种模式）
├── start.sh                 # 本地一键启动脚本
├── Dockerfile               # Python 3.11-slim 镜像（Gunicorn 生产）
├── docker-compose.yml       # 通用 Docker Compose 配置
├── requirements.txt         # Python 依赖清单
│
├── strategies/              # 策略层（15 个注册策略）
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
│   ├── composite_backtest.py         # 全策略组合回测
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

---

## 2. 核心模块

### 2.1 策略层（Strategies）

#### 2.1.1 基类 `BaseStrategy`

采用**模板方法模式**，子类只需实现 `_evaluate_single_stock()` 方法。

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

**关键特性：**
- `StockSignal` dataclass 封装信号详情（评分、胜率、触发信号、风险标签）
- `_SkipStock` 内部异常：数据不足时抛出，该股票不计入 scanned 计数
- `prepare_for_date()` hook：横截面策略（如 reversal）的全市场预处理入口
- `set_progress_callback()`：进度回调支持

#### 2.1.2 策略注册表 `STRATEGY_REGISTRY`

16 个策略按**语义簇**分组注册，每组内含权重配置：

| 分组 | ID 数 | 策略 |
|------|-------|------|
| G1 趋势顺势 | 6 | macd_bull, right_side, strong_stock, golden_cross, tail_market, momentum |
| G2 反转超跌 | 4 | td_sequential, rsi_oversold, bollinger_lower_bounce, reversal |
| G3 突破·创新高 | 5 | volume_breakout, bollinger_breakout, rps_breakout, high_tight_flag, limit_up_gene |
| G4 形态独立 | 1 | chanlun_strict（Jaccard < 8%，最独立） |

每个注册项包含：`cls`, `name`, `description`, `tags`, `icon`, `weight`, `group`。

#### 2.1.3 策略扩展指南

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

### 2.2 引擎层（Core）

#### 2.2.1 `ScreenEngine` — 选股引擎

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

**评分模型：**

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

#### 2.2.2 `MarketScanner` — 数据扫描器

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

**预加载策略（3 轮并发 + 串行兜底）：**
- 第 0 轮：批量实时快速路径（腾讯 90只/HTTP，补全只缺今日一行的标的）
- 第 1 轮：高并发（50 workers）
- 第 2 轮：中并发（20 workers）
- 第 3 轮：低并发（10 workers）
- 第 4 轮：极低并发（5 workers）
- 最终：串行兜底（1 worker，间隔 1s 防限流）

#### 2.2.3 `Observability` — 可观测性收集器

线程安全的环形缓冲（默认 1000 条事件），记录：
- 错误/警告/调试事件
- 阶段耗时（`obs.timer()` 上下文管理器）
- 按 level/source 的计数器

```python
obs.error("data.fetch", "fetch_one", "failed: timeout", context={"code": "600519"})
with obs.timer("stage", "prefetch_klines", context={"total": 317}):
    ...  # 自动记录耗时
summary = obs.summary()  # 事件摘要 + 慢操作 Top5
```

#### 2.2.4 `RiskScanner` — 风险扫描

轻量风险/卖出/买入风险评估，仅读取已缓存 K 线数据：

```python
def quick_risk_scan(code, df=None) -> (tag, reasons, score, flags)
    # tag ∈ {"safe", "watch", "conflict", "high_risk", "unknown"}

def quick_sell_scan(code, df=None) -> dict
    # 检测 MACD 死叉/MA 空头排列/RSI 超买/布林上轨/TD 卖出计数

def assess_buy_risk(code, df=None) -> dict
    # 买入风险评估：卖出信号强 = 买入风险高
```

#### 2.2.5 集中配置 `Constants`

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

### 2.3 数据层（Data）

#### 2.3.1 多数据源适配 `DataSources`

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

#### 2.3.2 数据更新决策 `DataLayer`

6 层设计：
1. 判断数据是否需要更新
2. 需要更新实时还是收盘数据
3. 实时→新浪/腾讯，收盘→Baostock
4. Baostock 写入本地，合并到历史数据
5. 交易时间→实时数据，非交易时间→收盘数据
6. 通达信离线 `.day` 兜底

#### 2.3.3 交易日历 `MarketCalendar`

- 硬编码官方休假日（2025-2026）
- Baostock 校正缓存（`trade_calendar.json`，只增不减）
- 统一来源：`is_market_open()`, `is_holiday()`, `get_last_trading_date()`

#### 2.3.4 失效股黑名单 `DelistedRegistry`

- 自动登记「无历史 + 全源失败」的死码
- TTL 90 天，到期自动重探
- 区分停牌与退市，避免误杀复牌股票

### 2.4 技术指标库（Utils）

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

### 2.5 持仓管理（Portfolio）

#### 2.5.1 `Portfolio` — 虚拟持仓

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

#### 2.5.2 `SellAnalyzer` — 卖出信号分析器

五维分析 → 五级建议：

| 维度 | 信号 | 权重 |
|------|------|------|
| 止盈 | RSI 超买、触及布林上轨、连续高位滞涨 | 15-25 |
| 止损 | 跌幅>10%/5%/跌破MA5/MA20 | 8-40 |
| 警示 | 量价背离、MACD 顶背离、TD 九转卖出 | 10-20 |
| 趋势 | MA 空头排列、跌破关键支撑 | 10-20 |
| 风险 | 跌停/巨量/乌云盖顶/高位十字星 | 12-25 |

五级建议：`HOLD` → `WATCH` → `REDUCE` → `SELL` → `URGENT`

### 2.6 Web 服务（Server）

Flask + CORS + SSE（Server-Sent Events）：

```
┌──────────┐    HTTP POST     ┌────────────┐
│  Frontend│ ──────────────►  │  /api/screen│
│  (HTML)  │                  │  (async)   │
│          │◄───────────────  │            │
│          │   SSE Stream     │            │
└──────────┘                  └────────────┘
                                │
                         Thread Pool Executor
                                │
                    ┌───────────┼───────────┐
                    │           │           │
              Strategy 1   Strategy 2   ...
              (done)       (done)
                    │           │
                    ▼           ▼
              SSE broadcast to all connected clients
```

**关键机制：**
- 筛选任务异步执行（`ThreadPoolExecutor`），不阻塞 API 线程
- SSE 流式推送策略完成事件和进度更新
- 筛选结果持久化到 `data/last_screen_result.json`，重启恢复
- 15 分钟超时保护
- 停止按钮：`threading.Event` + 5 秒等待 + 强制重置

---

## 3. 数据流

### 3.1 实时筛选流程

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

### 3.2 数据获取流程

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

### 3.3 持仓管理数据流

```
用户点击"买入"
    │
    ▼
Portfolio.buy(code, price, shares, ...)
    │
    ├─► 扣除现金
    ├─► 添加到 positions
    ├─► 记录到 trades
    └─► 持久化到 portfolio.json
    │
    ▼
每次筛选时自动评估持仓
    │
    ├─► 获取持仓股最新价格
    ├─► 计算盈亏（浮盈/已实现盈亏）
    ├─► 对每只持仓运行 SellAnalyzer
    │     ├─► RSI/MACD/布林带/均线/风险标志
    │     └─► 综合评分 → HOLD/WATCH/REDUCE/SELL/URGENT
    └─► 返回持仓概览 + 卖出建议
```

---

## 4. 策略体系

### 4.1 策略全景

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

### 4.2 策略互斥与互补

- **同组衰减**：G1 内多策略同时命中同一股票时，最高分者全额计分，其余 × 0.3
- **跨组共识**：不同组的策略同时命中，获得额外共识加成（每多一个命中策略 +5%，上限 15%）
- **Jaccard 独立性**：G4 缠论与其他全部策略的 Jaccard 交集 < 8%，是最独立的策略

### 4.3 风险过滤

每只推荐股票附带多维风险评估：
- **风险标签**：safe / watch / conflict / high_risk
- **风险标志**：跌停/巨量/乌云盖顶/高位十字星/放量下跌等
- **卖出信号**：MACD 死叉 / MA 空头排列 / RSI 超买 / TD 卖出计数
- **买入风险**：综合卖出信号强度给出买入风险等级

---

## 5. 回测流程

### 5.1 回测架构

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

### 5.2 关键设计

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

### 5.3 回测工具

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

---

## 6. 依赖与运行方式

### 6.1 系统要求

- **Python 3.11**（系统 python3 可能是 3.7，不可用）
- **操作系统**：macOS / Linux / WSL

### 6.2 依赖清单

```
# Web 服务
Flask>=3.0,<4.0
flask-cors>=4.0

# 数据/计算（全部 pandas/numpy 自实现，无 TA-Lib）
pandas==2.2.3        # 钉死 2.2.x
numpy==1.26.4        # 钉死 1.26.x
baostock>=0.9.0
requests>=2.31

# 测试
pytest>=7.0.0
```

### 6.3 安装与启动

#### 方式一：本地一键启动

```bash
# 首次安装
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt

# 启动（自动安装依赖 + 检查端口 + 打开浏览器）
bash start.sh
# 访问 http://127.0.0.1:5188
```

#### 方式二：直接 Python 启动

```bash
# Web 服务
.venv/bin/python main.py web [--port 5188] [--host 0.0.0.0] [--debug]

# 命令行筛选
.venv/bin/python main.py screen -s macd_bull strong_stock -m 主板 -n 10

# 列出所有策略
.venv/bin/python main.py list

# JSON 输出
.venv/bin/python main.py screen -s macd_bull --json
```

#### 方式三：Docker 部署

```bash
# 本地开发
docker compose up --build

# 阿里云 ECS
docker compose -f docker-compose.yml -f docker-compose.alibaba.yml up -d

# 腾讯云 CVM
docker compose -f docker-compose.yml -f docker-compose.tencent.yml up -d

# 生产模式（Gunicorn）
docker run -p 5188:5188 gezhe:latest
```

Docker 镜像基于 `python:3.11-slim`，生产使用 Gunicorn（2 workers × 4 threads）。

### 6.4 回测运行

```bash
# 快速测试
.venv/bin/python backtest/backtest_quick.py

# 完整回测
.venv/bin/python backtest/backtest_engine.py

# 组合回测
.venv/bin/python tools/composite_backtest.py
```

---

## 7. API 参考

### 7.1 REST API

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

### 7.2 SSE 事件流

筛选过程中通过 SSE 推送以下事件：

| 事件 | 数据 |
|------|------|
| `progress` | `{phase, current, pct, strategies: {name: {status, pct, hits, scanned}}}` |
| `strategy_done` | `{name, data: {strategy_name, hit_count, top_stocks}}` |
| `done` | `{comprehensive_picks, strategy_details}` |
| `error` | `{error: string}` |

### 7.3 CLI 命令

```bash
# 列出所有策略
python main.py list

# 运行所有策略（默认主板 Top10）
python main.py screen

# 运行指定策略
python main.py screen -s macd_bull strong_stock

# 指定市场 + 数量
python main.py screen -m 创业板 -n 5

# JSON 输出
python main.py screen -s macd_bull --json

# Web 模式
python main.py web --port 5188 --debug
```

---

## 8. 运维与部署

### 8.1 缓存管理

| 缓存位置 | 内容 | 说明 |
|----------|------|------|
| `data/cache/klines/*.csv` | K 线数据 | 每只股票一个文件 |
| `data/cache/stocks.json` | 股票列表 | 全市场 A 股 |
| `data/cache/meta.json` | 缓存元数据 | 更新时间/条数 |
| `data/cache/trade_calendar.json` | 交易日历校正 | Baostock 校正结果 |
| `data/cache/delisted_blocklist.json` | 失效股黑名单 | TTL 90 天 |
| `data/last_screen_result.json` | 上次筛选结果 | 启动时恢复 |
| `data/portfolio.json` | 虚拟持仓 | 持久化持仓数据 |

### 8.2 监控与诊断

通过 `Observability` 收集器可查询：
- `obs.recent(n=200)` — 最近 N 条事件
- `obs.counters()` — 按 level/source 的计数
- `obs.summary()` — 摘要（事件总数/错误分布/慢操作 Top5）

### 8.3 故障恢复

| 场景 | 恢复机制 |
|------|---------|
| 服务重启 | 自动恢复上次筛选结果 + 持仓数据 |
| 网络不可用 | 降级到本地 CSV 缓存 / 通达信离线 |
| 单数据源失败 | 自动切换到备用数据源（新浪→腾讯→东财→Baostock） |
| 并发全部失败 | 逐步降级并发数（50→20→10→5→串行） |
| 失效股票 | 自动登记黑名单，90 天后重探 |
| 筛选中断 | 15 分钟超时保护，强制重置 |

### 8.4 部署脚本

| 脚本 | 用途 |
|------|------|
| `deploy-alibaba.sh` | 阿里云 ECS 一键部署 |
| `deploy-tencent.sh` | 腾讯云 CVM 一键部署 |
| `start.sh` | 本地一键启动 |
| `Dockerfile` | 容器镜像构建 |
| `docker-compose.yml` | 通用 Docker Compose |
| `docker-compose.alibaba.yml` | 阿里云覆盖配置 |
| `docker-compose.tencent.yml` | 腾讯云覆盖配置 |
