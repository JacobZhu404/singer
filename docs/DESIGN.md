# 歌者 选股系统 — 设计文档（自顶向下 + 现状对照 + 整合方案）

> 本文档先给出"工程应该长什么样"的理想设计，再对照现有实现标注
> ✅已实现 / ⚠️冗余 / ❌缺失 / 🗑️死代码，最后给出**不丢功能**的整合方案。
> 整合开发请以本文档为准；改动后回到对应小节打勾。

---

## 0. 目标与范围

- **核心目标**：从全市场 A 股中，按多个量化策略并行筛选，综合评分给出推荐买入列表；并对持仓做卖出/风险提示。
- **数据来源**：腾讯/新浪/东财（实时+历史）、Baostock（收盘后复权）、通达信本地日线（离线兜底）。
- **交付形态**：Web（Flask + SSE 进度）+ 命令行（`main.py screen/list/web`）。
- **非目标**（当前阶段不做）：实盘下单、分钟级 tick、Tushare Pro（需 token，列为 P2）。

---

## 1. 理想分层架构

```
┌─ 表现层 ─────────────────────────────────────────────┐
│ web/templates/index.html  (UI + SSE + 诊断面板)        │
│ core/server.py            (Flask 路由, 仅做编排/序列化) │
│ main.py                   (CLI 入口)                   │
└──────────────────────────────────────────────────────┘
            │ 调用
┌─ 引擎层 ─────────────────────────────────────────────┐
│ core/engine.py     ScreenEngine: 下载→并行策略→合并评分 │
│ core/risk_scanner  风险/卖出/买入快扫（已拆分 ✅）       │
│ core/constants.py  集中常量                            │
│ core/observability 全局事件/错误/耗时（已建 ✅）        │
└──────────────────────────────────────────────────────┘
            │ 调用
┌─ 策略层 ─────────────────────────────────────────────┐
│ strategies/base.py  BaseStrategy: 模板方法 + 并行 screen│
│ strategies/*.py     各策略只实现 _evaluate_single_stock │
│ strategies/registry 注册表（唯一真相源）                │
└──────────────────────────────────────────────────────┘
            │ 取数（唯一入口）
┌─ 数据层 ─────────────────────────────────────────────┐
│ data/fetcher.py     MarketScanner: 三层缓存 + 对外门面  │
│ data/<sources>      具体数据源 + 多源管理/降级           │
│ data/local_cache.py CSV+meta 缓存                       │
└──────────────────────────────────────────────────────┘
            │ 复用
┌─ 工具/回测/持仓 ─────────────────────────────────────┐
│ utils/indicators, sell_signals, market_trend, precalc  │
│ backtest/           历史回测（应复用策略层，不另写一份） │
│ portfolio/manager   虚拟持仓                            │
│ tools/              一次性运维脚本（通达信导入等）        │
└──────────────────────────────────────────────────────┘
```

**铁律（设计约束）**

1. **数据层只有一个对外门面**：`MarketScanner`（`data/fetcher.py`）。引擎/策略/回测/持仓只能经它取数，不得直接 import 具体数据源。
2. **策略逻辑只写一份**：在 `strategies/*.py`。回测必须**调用真实策略**，不得重写评分。
3. **注册表是策略的唯一真相源**：能被筛选、被回测的策略 = `STRATEGY_REGISTRY` 的键。文件存在但未注册 = 要么注册、要么删。
4. **市场时间/交易日判断只写一份**，全工程共用。
5. **实时行情→K线合成只写一份**。
6. **except 不得静默**（已落地 observability 约定）。

---

## 2. 理想数据流（下载→筛选→推荐）

```
download_data(force_refresh)
  └─ MarketScanner.prefetch_batch(codes)
       ├─ 决策每只是否需要更新 (NO_UPDATE/REALTIME/CLOSE)
       ├─ 需要更新的 → 真·批量接口（腾讯 90 code/请求）一次拿回
       │     · 历史K线：本地CSV/通达信为底，批量实时只补"今日一行"
       │     · 写入：完整历史 df 进内存缓存（绝不是 1 行）
       └─ 失败的 → 多源降级（腾讯→新浪→东财→baostock→本地旧缓存）
  └─ get_recommendation()
       ├─ 串行跑各策略（策略内部 ThreadPool 并行 evaluate）
       ├─ merge_results() 加权 + 排名分 + 市场强弱调整
       └─ 风险/卖出快扫 → 输出推荐列表
```

**关键不变量**：内存缓存 `_kline_cache[code]` 永远是"完整历史 df"，盘中实时只是**末尾追加/替换今日一行**，绝不能整体被 1 行覆盖。

---

## 3. 现状对照表

### 3.1 数据层（问题最集中）

| 理想 | 现有文件 | 状态 | 说明 |
|---|---|---|---|
| 唯一门面 MarketScanner | `data/fetcher.py` | ✅ | 对外入口正确 |
| 三层缓存 内存→CSV→网络 | `fetcher.get_history` + `local_cache` | ✅ | |
| 真·批量下载 | ~~`data_layer.get_batch()`~~ | ✅ 已删 | 阶段1：零调用，2026-06-18 删 |
| 单只取数 | `data_layer.get_kline(单code)` | ✅ 已修 | C1：`prefetch_batch` 新增 `_batch_realtime_path` 快速路径，REALTIME 类 code 一次批量 HTTP（90/req）补今日；CLOSE 类才进单只链路 |
| 多源降级 | `data_sources.DataSourceManager` | ✅ 已修 | C3：CLOSE 分支首选 baostock，失败/空时降级到多源链（Sina/Tencent/Eastmoney） |
| 实时数据源适配 | `data/tencent_realtime.py` | ✅ 已修 | C2 修复：`get_kline` 现取本地完整历史后 merge 今日，不再返回 1 行 |
| 腾讯批量底层 | `data/tencent_batch.py` | ✅ | 真批量在这（90/请求），但上层没用批量入口 |
| 收盘后复权 | `data/baostock_source.py` | ✅ | CLOSE 分支只走它、无降级 ⚠️ |
| 通达信离线兜底 | `data/tdx_offline.py` | ✅ | `get_stock_history` 第0层 |
| CSV+meta 缓存 | `data/local_cache.py` | ✅ | |
| 市场时间判断 | `data/market_calendar.py`（唯一） | ✅ 已收口 | B2：fetcher / data_layer 全部 re-export，legacy datetime 版本仅 1 处内部用 |
| 实时→K线合成 | `data/realtime_merge.py`（唯一） | ✅ 已收口 | B1：fetcher.`_merge_today_realtime` 委托；data_layer.`_convert_realtime_to_kline` 已删；tencent_realtime.`get_kline` 改用此模块 |
| 一次性同方达导入 | `tools/import_tdx.py` | ✅ 已迁 | 阶段1：从 data/ 迁到 tools/，路径已修 |
| 同方达解析器（备用） | ~~`utils/tonghuada_parser.py`~~ | ✅ 已删 | 阶段1：零引用，2026-06-18 删 |

### 3.2 策略层

| 策略 | 文件 | 注册表 | 回测可达 | 状态 |
|---|---|---|---|---|
| macd_bull / strong_stock / td_sequential / right_side / rsi_oversold / bollinger_bands / volume_breakout / chanlun_strict / momentum / golden_cross | ✅ | ✅ | ✅(重写版) | 正常但回测是另写的 |
| limit_up_gene | `strategies/limit_up_gene.py` | ❌(import 了没进 dict) | ❌ | **孤儿**：要么注册要么删 |
| chan20 | `strategies/chan20.py` | ❌ | ⚠️(只在死脚本/`_strategy_check`) | **半孤儿**：注册或删 |

### 3.3 引擎/表现/工具层

| 模块 | 状态 | 说明 |
|---|---|---|
| `core/engine.py` (1010 行) | ✅ | risk_scanner 已拆出；仍偏大可继续拆 |
| `core/server.py` | ✅ | 路由清晰 |
| `core/observability.py` | ✅ | 已建 + 前端诊断面板 |
| `backtest/backtest_engine.py` | ⚠️ | **`_strategy_check` 内联重写了 10+ 策略评分**，与策略层重复，会漂移 |
| ~~`backtest/backtest_5day_strong.py` / `param_grid_search.py` / `test_strict_chanlun.py` / `backtest_raw_counts.py`~~ | ✅ 已删 | 阶段1，2026-06-18 |
| ~~顶层 `backtest_run.py` / `grid_search_chan20.py`~~ | ✅ 已删 | 阶段1，2026-06-18 |
| ~~`data/cache/meta.json.bak`~~ | ✅ 不存在 | 已在 git status 中标记为 untracked，未提交过 |

---

## 4. 问题清单（按严重度）

### A. 死代码（删除/归档，低风险）✅ 已完成（2026-06-18 阶段1）
- ✅ ~~`utils/tonghuada_parser.py`~~、~~`data/cache/meta.json.bak`~~
- ✅ ~~`backtest/backtest_5day_strong.py`~~、~~`param_grid_search.py`~~、~~`test_strict_chanlun.py`~~、~~`backtest_raw_counts.py`~~
- ✅ 顶层 ~~`backtest_run.py`~~、~~`grid_search_chan20.py`~~
- ✅ ~~`data_layer.DataFetcher.get_batch()`~~（死方法）
- ✅ `data/import_tdx.py` → `tools/import_tdx.py`（路径已修；CSV 输出仍写 `data/cache/klines/`）

### B. 冗余逻辑（合并到单份，中风险）✅ 已完成（2026-06-18 阶段2）
- ✅ **B1 实时→K线合成** → 收口到 `data/realtime_merge.py`（`merge_realtime_into_history` + `normalize_quote` + `estimate_full_day_volume`）。原 3 份：fetcher 委托 / data_layer 已删 / tencent_realtime 已改正（含 C2 修复）。新增 8 个单测 `tests/test_realtime_merge.py`。
- ✅ **B2 市场时间/交易日** → 收口到 `data/market_calendar.py`。fetcher / data_layer 全部 re-export 同一实现。`get_last_trading_date` 返回 date；旧 datetime 版本仅保留 1 个内部 helper `_get_last_trading_date_legacy`（兼容 fetcher.check_data_freshness）。

### C. 数据链（核心修复，高风险高收益）✅ 已完成（2026-06-18 阶段4）
- ✅ **C1 批量退化** → `data/fetcher.py:_batch_realtime_path`：对 REALTIME 决策的 codes，一次性调 `tencent_batch.get_realtime_fast`（90/HTTP × 5 并发），merge 进各自完整历史后写回内存缓存；CLOSE 才回退到 `_fetch_round` 单只链路。"卡在 200/317"问题在批量化后大幅缓解。
- ✅ **C2 缓存污染 bug**：`tencent_realtime.get_kline` 现先取本地完整历史再 merge 今日，返回完整 df。测试 `test_realtime_merge.py::test_empty_history_returns_empty` 防回归。
- ✅ **C3 CLOSE 分支无降级** → `data/data_layer.py:DataFetcher.get_kline`：首选 baostock；失败/空时自动降级到 `DataSourceManager`（Sina/Tencent/Eastmoney）。
- ✅ **C4 重试惩罚**：批量快速路径承担了大头，单只链路只剩 CLOSE/残留少量 code，sleep 总开销可忽略。

### D. 策略层一致性（中风险）
- **D1 回测重写策略** → 让回测通过"as-of 适配器"调用**真实策略**（见 §5.3），删 `_strategy_check` 内联评分。
- **D2 孤儿策略**：`limit_up_gene`、`chan20` → 决定注册或删，二选一，不留中间态。

---

## 5. 整合方案（分阶段，保证不丢功能）

> 原则：**先文档、后动手；每阶段独立可回归（pytest 全绿）；删除前先确认替代路径已存在。**

### 阶段 1：死代码清理（A）— 低风险，先做
1. 新建 `tools/`，移 `import_tdx.py` 进去，更新其内部相对路径与文档。
2. 删除 §4-A 列出的独立脚本与 `tonghuada_parser.py`、`meta.json.bak`、`get_batch()`。
3. **删除前检查**：`grep` 确认零 import；`pytest` 全绿；`main.py web/screen/list` 能起。
4. README 项目结构同步。

### 阶段 2：策略层收口（D）
1. `limit_up_gene`、`chan20`：逐个决策。保留则补进 `STRATEGY_REGISTRY` 并确保 `_evaluate_single_stock` 完整；删除则连带清理 import 与死脚本引用。
2. 删 `core/engine.py` 里 `k != "limit_up_gene"` 这类对不存在键的防御过滤。

### 阶段 3：回测复用真实策略（D1）— 关键防漂移
1. 设计 `BacktestScanner` 适配器：实现与 `MarketScanner` 相同的 `get_history(code)` 接口，但**只返回 ≤ trade_date 的切片**（as-of 视图），避免未来函数。
2. 回测 `_process_one` 改为：`strategy = get_strategy(name); sig = strategy._evaluate_single_stock(code, backtest_scanner, name_map, trade_date)`，用 `sig.score` 判定。
3. 删除 `backtest_engine._strategy_check` 内联评分。
4. **回归**：同一历史区间，整合前后跑一遍，命中数/收益曲线偏差在容忍范围内（记录差异原因）。

### 阶段 4：数据链重构（C）— 收益最大
1. 新建/明确 `data/market_calendar.py`，合并市场时间判断（B2）。
2. `prefetch_batch` 改造：
   - 历史：本地 CSV/通达信为底（已有）。
   - 今日：一次 `tencent_batch.get_realtime_fast(全部codes)` 批量补今日行。
   - 写入：完整历史 df 进内存缓存（修 C2）。
3. 失败/盘后：走 `DataSourceManager` 降级链（修 C3），CLOSE 分支接降级。
4. 合并实时→K线为一份（B1），删另外两份。
5. **回归**：盘中/盘后两场景各跑一次 `download_data` + 一个策略，确认内存缓存条数 = 完整历史，不是 1 行；诊断面板无大量 fetch_one error。

### 阶段 5：文档收尾 ✅ 已完成（2026-06-18）
1. ✅ 本 DESIGN.md §3.1 / §4-A / §4-B / §4-C 全部勾选最新状态。
2. ✅ `docs/skills.md` 已写——项目地图、数据流（含 C1 快速路径）、调试入口、常见任务配方、硬约束、已知坑、AI 接手指南。

---

## 6. 回归保障

- **现有测试**：`pytest tests/`（38 用例，含 observability/risk_scanner/web 烟测/回测成本）必须始终全绿。
- **新增建议**：
  - `tests/test_data_flow.py`：mock 数据源，断言 `prefetch_batch` 后内存缓存是完整历史而非 1 行。
  - `tests/test_backtest_uses_real_strategy.py`：断言回测与 live 对同一 df 给出一致评分。
- **手动验收**：`main.py web` 起服务，盘中/盘后各跑一次，看诊断面板 + 进度条不卡尾。

---

## 7. 决策待确认项

1. `limit_up_gene` / `chan20`：注册启用，还是删除？（影响阶段 2）
2. 多源降级是否要把 baostock 也纳入统一 `DataSourceManager`，还是保持 CLOSE 专用？
3. 是否新建 `data/market_calendar.py`（推荐），还是把时间判断并入 `fetcher`？
