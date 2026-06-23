# 「歌者」选股系统 — 技能手册（给大模型 / CLI / 新人）

> 这是给 **AI 助手 / 命令行 / 新接手开发** 用的"项目即时上手"指南。
> 设计与重构方案见 `docs/DESIGN.md`。本文只讲：**项目长什么样、数据怎么流、出问题去哪查、常见任务怎么干。**

---

## 1. 项目一句话

A 股全市场量化筛选器：批量下载行情 → 并行跑策略 → 综合评分 → Web/CLI 推荐买入列表。

---

## 2. 项目地图（你最常碰的文件）

```
stock_screener/
├── main.py                          # CLI 入口：screen / list / web
├── core/
│   ├── server.py                    # Flask 路由 + SSE 进度
│   ├── engine.py                    # ScreenEngine：下载→并行策略→合并评分
│   ├── risk_scanner.py              # 持仓风险/卖出/买入快扫
│   ├── observability.py             # 全局事件/错误/耗时收集器
│   └── constants.py                 # 集中常量（不要再硬编码到代码里）
│
├── strategies/
│   ├── base.py                      # BaseStrategy 模板方法 + 并行 screen
│   ├── registry.py                  # STRATEGY_REGISTRY：策略唯一真相源
│   └── *.py                         # 各策略，只实现 _evaluate_single_stock
│
├── data/                            # 数据层（铁律：只能通过 fetcher 取数）
│   ├── fetcher.py                   # MarketScanner：三层缓存 + 对外门面
│   ├── data_layer.py                # DataFetcher：决策 + 单只取数（fetcher 内部用）
│   ├── data_sources.py              # DataSourceManager：Sina/Tencent/Eastmoney + 健康度
│   ├── tencent_batch.py             # 腾讯批量实时（90 code/请求）
│   ├── tencent_realtime.py          # 实时适配器（本地历史 + merge 今日）
│   ├── baostock_source.py           # Baostock 收盘后复权（CLOSE 首选）
│   ├── tdx_offline.py               # 通达信本地日线（最底层兜底）
│   ├── local_cache.py               # CSV + meta.json 缓存
│   ├── market_calendar.py           # ✅ 唯一交易时间/交易日判断
│   └── realtime_merge.py            # ✅ 唯一"实时报价 + 历史 K线"合成
│
├── backtest/                        # 历史回测（注意：当前 _strategy_check 内联评分，待统一）
├── portfolio/                       # 虚拟持仓
├── utils/                           # 指标、卖出信号、市场强弱、预计算
├── tools/                           # 一次性运维脚本（如 import_tdx.py）
├── web/templates/index.html         # UI + SSE + 诊断面板
├── tests/                           # pytest 套件（必须始终全绿）
└── docs/
    ├── DESIGN.md                    # 顶层设计 + 整合方案 + 现状对照
    └── skills.md                    # 本文档
```

---

## 3. 数据流（必背 60 秒）

```
用户点"开始筛选"
  ↓ POST /api/screen   (core/server.py)
ScreenEngine.get_recommendation()  (core/engine.py)
  ↓
download_data(stocks, force_refresh)
  └─ MarketScanner.prefetch_batch(codes)        # data/fetcher.py
       ├─ 读 meta.json 决定每只 update_type
       ├─ ★ REALTIME 类 → _batch_realtime_path     # ★ C1 快速路径
       │        tencent_batch.get_realtime_fast(全部 codes, 5 并发)
       │        每只 merge 进本地完整历史 → 写内存缓存
       └─ CLOSE / 残留 → _fetch_round (单只并发)
                            baostock 优先；失败降级 DataSourceManager (Sina→Tencent→EM)
  ↓
串行 for strategy in [macd_bull, strong_stock, ...]:
   strategy.screen(scanner, name_map)          # strategies/base.py
     └─ ThreadPoolExecutor 并行 _evaluate_single_stock
  ↓
merge_results()                                # 多策略加权 + 排名分 + 市场强弱
  ↓
risk_scanner 卖出/买入快扫
  ↓
返回 JSON / SSE 推送进度
```

**关键不变量**：
- `_kline_cache[code]` 永远是**完整历史 df**。实时只是在末尾追加/替换今日 1 行，**绝不能整体被 1 行覆盖**（这是 §C2 修过的污染 bug）。
- 数据层对外只有一个门面：`MarketScanner`。引擎/策略/回测/持仓都不要直接 import 具体数据源。

---

## 4. 调试入口（坏了第一时间去这）

| 现象 | 第一时间看 |
|---|---|
| Web 起不来 | `python3 main.py web` 终端日志；端口冲突 → `--port` |
| 筛选卡住不动 | 浏览器右下角"诊断面板"（实时 obs 事件流）→ 看哪个 source 在刷 error |
| 停止按钮没反应 | 看 `core/server.py:api_screen_stop`（5s 内 poll + 强制重置）和 `data/fetcher.py:_fetch_round`（pool.shutdown(wait=False)） |
| 内存缓存怀疑被污染 | `len(scanner._kline_cache[code])` 应远大于 1；若 == 1 立即看 `data/tencent_realtime.py:get_kline` 是否退化 |
| 某只股票数据怪 | `data/cache/klines/<code>.csv` 是真相；`data/cache/meta.json` 看 last_update |
| 策略推不出股票 | 单独跑 `python3 main.py screen -s <name> -n 50` 加 `--json` |
| 多源都失败 | `data_manager.get_health_status()` 看健康度（也在诊断面板展示） |

**Observability 用法**（写代码时）：
```python
from core.observability import obs
obs.info("data.fetch", "batch_done", "本轮完成", context={"ok": 80, "fail": 2})
obs.warn("data.fetch", "fallback", "baostock 空，降级多源", context={"code": code})
obs.error("data.fetch", "fetch_one", str(e), context={"code": code}, exc=e)
with obs.timer("data.batch", "tencent_realtime_fast", context={"n": len(codes)}):
    quote_map = get_realtime_fast(codes)
```
**禁止** `except: pass` —— 至少 `obs.error(...)`。

---

## 5. 常见任务配方

### 5.1 加一个新策略
1. `strategies/my_strategy.py`：
   ```python
   from .base import BaseStrategy, StockSignal
   class MyStrategy(BaseStrategy):
       name = "my_strategy"
       description = "..."
       base_win_rate = 0.55
       def _evaluate_single_stock(self, code, scanner, name_map, trade_date):
           df = scanner.get_history(code)      # ★ 永远走 scanner，不要碰数据源
           if df is None or len(df) < 30: return None
           score = ...  # 0-100
           if score >= 50:
               return StockSignal(code=code, name=name_map.get(code,""),
                                  score=score, price=float(df["close"].iloc[-1]),
                                  reason="...")
           return None
   ```
2. `strategies/registry.py`：在 `STRATEGY_REGISTRY` 中注册（带 weight/tags/icon）。
3. 跑一下：`python3 main.py screen -s my_strategy --json`。
4. **铁律**：策略文件存在但未注册 = 死代码。要么注册要么删，别留中间态。

### 5.2 加一条 CLI 命令
- 编辑 `main.py` 的 argparse 子命令；调用 `ScreenEngine` 或 `MarketScanner`，不要绕过它们直接 requests。

### 5.3 调试"为什么这只股票没被选上"
```bash
python3 main.py screen -s macd_bull --json | jq '.[] | select(.code=="600519")'
# 没有 → 策略给的分 < 50；看策略文件的 _evaluate_single_stock
# 或者数据不全 → 看 data/cache/klines/600519.csv
```

### 5.4 加一个数据源
1. 在 `data/data_sources.py` 加个 `class FooDataSource(DataSource)`，实现 `get_kline / get_realtime`。
2. 在 `DataSourceManager.__init__` 的 `self._sources` 列表里按优先级插入。
3. 健康度框架（连续失败 → 跳过 N 分钟）自动生效。

### 5.5 一次性导入通达信日线
```bash
python3 tools/import_tdx.py            # 写入 data/cache/klines/
```

### 5.6 跑测试
```bash
python3 -m pytest tests/ -x -q
# 必须始终全绿。47 用例左右。
```

---

## 6. 写代码时的硬约束

1. **数据层只有一个对外门面 `MarketScanner`**。引擎/策略/回测/持仓只能走它取数。
2. **策略只写一份**：回测必须**调用真实策略**（as-of 适配器），不得在 backtest 里重写评分。当前 `backtest_engine._strategy_check` 是历史遗留，待统一。
3. **注册表是策略唯一真相源**：`STRATEGY_REGISTRY` 的键 = 可被筛选/回测的策略。
4. **市场时间/交易日判断**只用 `data/market_calendar.py`。不要再写第 4 份。
5. **实时 → K线合成**只用 `data/realtime_merge.py`。不要再写第 4 份。
6. **except 不得静默**：用 `obs.error(..., exc=e)`，不要 `pass`。
7. **硬编码常量进 `core/constants.py`**：`MAX_WORKERS_*`、`DEFAULT_KLINE_DAYS`、`MIN_SINGLE_SCORE` 等已在。
8. **stocks.json 的字段是 `ts_code`/`name`**（旧的 `代码`/`名称` 已废弃）。
9. **线程安全**：`MarketScanner._kline_cache` 访问要 `self._lock`。
10. **不要 `with ThreadPoolExecutor() as pool:` 跑可取消的工作** —— `__exit__` 会 `wait=True` 卡住停止。要 `pool = ThreadPoolExecutor(...); try: ... finally: pool.shutdown(wait=False, cancel_futures=True)`。

---

## 7. 已知坑（踩过的，别再踩）

- **§C2 缓存污染**：`tencent_realtime.get_kline` 旧版只返回 1 行今日，调用方写进缓存就毁了完整历史。修法：先取本地完整历史再 merge。`tests/test_realtime_merge.py::test_empty_history_returns_empty` 防回归。
- **§C1 批量退化**：早期 `prefetch_batch` 对每只 code 单独 HTTP，盘中表现为"卡在 200/317"。修法：REALTIME 决策的全部走 `_batch_realtime_path`（腾讯批量 90/req）。
- **停止按钮卡 30 秒**：根因是 `with ThreadPoolExecutor` 的 `__exit__` 阻塞等所有 HTTP 完成。修法：手动 `shutdown(wait=False, cancel_futures=True)` + UI 5s 强制重置兜底。
- **`get_last_trading_date` 类型漂移**：曾有 datetime 和 date 两个签名各自存在，调用方 `.date()` 时会崩。当前已收口到 `data/market_calendar.py` 返回 `date`，legacy datetime 版本只 fetcher 内部一处用。
- **孤儿策略**：`limit_up_gene`（import 了没进 dict）/ `chan20`（半孤儿）。新增策略务必走 registry，不要留半孤儿。

---

## 8. 如果你是 AI 助手 接手任务

按以下顺序读：
1. **本文档（skills.md）** —— 5 分钟建立全图。
2. **`docs/DESIGN.md`** —— 看现状对照表（§3）和问题清单（§4）了解还有什么坑。
3. **`CLAUDE.md`** —— 项目级提示。
4. 看具体要改的文件之前，先 `git log -- <file>` 看最近 3-5 次改动的原因。

修改前自检：
- 我要不要新写文件？多半不需要 —— 优先编辑现有文件。
- 我有没有把数据访问绕过 `MarketScanner`？不要。
- 我有没有在 except 里静默？不要。
- 我有没有重新实现已收口的功能（市场时间/实时合成）？不要。
- 我有没有跑 `pytest tests/`？必须跑且全绿。
- 我有没有更新 `docs/DESIGN.md` 对应小节的 ✅ 状态？阶段性任务做完务必更新。
