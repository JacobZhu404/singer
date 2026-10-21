# 歌者 — 系统性代码审查报告

> 审查日期: 2026-07-01
> 审查范围: 全工程（注释、代码、策略、数据流、前端、效率、稳定性）
> 代码规模: Python 17,663 LOC / 策略 3,831 LOC / 前端 3,623 LOC / 测试 1,852 LOC
> 函数总数: 309个 / 有返回类型注解: 285个 (~92%)

---

## 一、代码质量（高优先级）

### 1.1 策略层大量重复代码（14个策略，Boilerplate 重复率 ~60%）

**问题**: 每个策略的 `_evaluate_single_stock` 都以完全相同的模式开头：
- `indicators = scanner.get_indicators(code, days=XXX)`
- `if not indicators or len(indicators["kline"]) < YY: raise self._SkipStock()`
- `df = indicators["kline"]; close = df["close"]`
- `signals = []; score = 0`
- 结尾再重复一遍 `StockSignal(...)` 构造（约15行）

**影响**: 新增策略需复制粘贴约40行模板代码；`days` 参数不一致（60/120/250）导致指标缓存碎片。

**建议**: 在 `BaseStrategy` 中提供 `_evaluate_with_indicators()` 钩子，把 `get_indicators → SkipStock → 解包` 逻辑统一收进基类。

### 1.2 裸 `except Exception` 过多（137处）

**问题**: 全项目有137处 `except Exception`，虽然大部分配合了日志记录，但可能吞掉真正的编程错误。

**关键风险点**:
- `data/fetcher.py` 中 `_fetch_round` 的 `future.result()` 异常捕获
- `core/server.py` 中几乎所有 API 端点的异常捕获
- `strategies/base.py` 中 `_eval_one` 的通用捕获

**建议**: 区分「预期异常」（网络超时、数据缺失）和「编程错误」（KeyError、TypeError）。对后者应让异常上浮或至少用 `logger.exception` 记录完整堆栈。

### 1.3 评分阈值硬编码为魔法数字

**问题**: 策略中的 `score < 90`、`score < 75`、`score < 60` 等阈值分散在15个策略文件中，无法统一调整。

**建议**: 每个策略定义 `MIN_SCORE` 类属性，或在 `constants.py` 中按策略分组定义。

### 1.4 `get_latest_trade_date()` 实现粗糙

**问题**: `data/fetcher.py:214` 直接返回 `datetime.now().strftime("%Y%m%d")`，周末/节假日会返回非交易日，导致信号日期错误。

**建议**: 复用 `market_calendar.py` 的交易日历，返回最近一个真实交易日。

---

## 二、稳定性与并发（高优先级）

### 2.1 `_is_running` 全局变量存在竞态条件（✅ 已修复）

**问题**: `core/server.py:53` `_is_running` 是全局裸变量，在 `api_screen()`、`run_task()`、`download_task()` 中被跨线程读写，**无锁保护**。两个并发请求可以同时通过检查并启动任务。

**修复**: 引入 `_task_lock = threading.Lock()`，对以下操作加锁：
- `api_screen()` / `api_download()` 启动前的 `_is_running` 检查
- `run_task()` / `download_task()` 内部的超时检测与 `_is_running = True`
- `finally` 中的 `_is_running = False`
- `api_screen_stop()` 的检查和轮询读取
- 强制重置 `_is_running = False`

### 2.2 `api_screen` 中超时判断逻辑（✅ 已复核，逻辑正确）

**说明**: `core/server.py:222`
```python
if _is_running and (time.time() - _task_start_time) < _task_timeout:
    return jsonify({"code": 1, "msg": "任务正在进行中，请稍后重试"})
```
语义：任务在跑 **且** 未超时（还在正常工作中）→ 拒绝新请求，返回"正在进行中"。如果已超时（可能卡死）→ 条件为 false → 落到下面 `_is_running = True` 开新任务。这是正确的"卡死恢复"逻辑。

### 2.3 Flask 后台任务无队列/无持久化

**问题**: 筛选任务用 `threading.Thread(daemon=True)` 启动，无任务队列。进程崩溃或重启，进行中的任务完全丢失。

**建议**: 短期：启动时恢复 `_screen_progress` 状态；长期：引入轻量任务队列。

### 2.4 LRU 缓存不是真正的 LRU

**问题**: `data/fetcher.py:461`
```python
evict_keys = list(self._kline_cache.keys())[:evict_n]
```
`dict` 的 `keys()` 顺序是插入顺序，不是最近使用顺序。

**建议**: 使用 `collections.OrderedDict` 或在每次 `get_history` 命中时 `move_to_end`。

### 2.5 指标缓存无上限

**问题**: `data/fetcher.py:400` `_indicator_cache` 无容量上限。

**建议**: 给 `_indicator_cache` 加 LRU 上限，或与 K 线缓存联动淘汰。

---

## 三、效率与性能（中优先级）

### 3.1 策略串行执行

**问题**: `ScreenEngine.screen_strategies()` 中策略是**串行**执行的。15个策略 × 每策略扫描全市场，总耗时长。

**建议**: 将策略分为无横截面依赖的（macd_bull 等）和有依赖的（reversal），前者可并行调度。

### 3.2 `merge_results` 阶段重复计算指标

**问题**: `core/engine.py:771-781` 每只入选股票在合并阶段又做了一套指标计算，而策略评估阶段 `get_indicators()` 已经算过。

**建议**: 将风险标志预计算并缓存到 `get_indicators` 的结果中，合并阶段直接读取。

### 3.3 持仓评估额外创建线程池

**问题**: `core/engine.py:1107-1108` `evaluate_positions` 自己管理了一个线程池，但 `BaseStrategy.screen()` 内部已有线程池。

**建议**: 复用 `BaseStrategy.screen()` 的并行能力，或改为串行（持仓通常只有几只）。

### 3.4 `get_indicators` days 不一致导致缓存碎片

**问题**: 14个策略中 days 参数不一致（60/120/250），`get_indicators` 的缓存 key 包含 days，不同 days 无法共享。

**建议**: 统一用最大需求值（如120）获取，策略内部按需切片。

---

## 四、前端（中优先级）

### 4.1 单HTML文件3623行，CSS+JS全部内联

**问题**: `web/templates/index.html` 一个文件包含完整 CSS（~580行）、HTML 结构、JavaScript（~2000+行），无模块拆分。

**建议**: 拆分为 `index.html` + `app.css` + `app.js`，引入构建工具或至少 CDN 加载。

### 4.2 策略 tooltip 数据与后端不同源

**问题**: 前端 `STRATEGY_TOOLTIPS` 对象（约1200行硬编码JS）与后端 `STRATEGY_REGISTRY` 和策略实际代码**不同源**。

**建议**: 后端 `/api/strategies` 返回评分规则元数据，前端动态渲染。

### 4.3 无响应式设计

**问题**: 侧边栏固定 `width: 340px`，在小屏幕上会溢出。

**建议**: 添加 `@media` 查询，小屏幕下改为垂直堆叠布局。

### 4.4 缺少前端错误边界

**问题**: JS 中大量 `try { ... } catch (e) {}` 静默吞错。

**建议**: 统一错误处理，展示 Toast 通知。

---

## 五、测试覆盖（中优先级）

### 5.1 核心引擎缺少单元测试

**现状**: 17个测试文件共1852行，但无 `test_engine.py` 覆盖 `ScreenEngine` 的核心流程。

**建议**: 补充 `test_engine.py`，mock `market_scanner`，测试 `merge_results` 的加权逻辑和组内去重。

### 5.2 Web API 测试覆盖不足

**现状**: `test_web_smoke.py` 仅98行，覆盖有限。

**建议**: 补充 API 端点的集成测试（使用 Flask test client）。

---

## 六、注释与文档

### 6.1 优点

- 模块级注释完善，设计意图说明清晰
- `BaseStrategy` docstring 详细说明了模板方法模式
- `engine.py` 中关键函数有 Args/Returns 注释
- 关键设计决策有注释说明（如 ThreadPoolExecutor 手动生命周期的原因）

### 6.2 小瑕疵

- 部分策略评分逻辑缺少「为什么这么给分」的注释
- `core/server.py` 的 `_PHASE_RANGES` 字典缺少注释
- `web/templates/index.html` 中的 JS 函数缺少 JSDoc

---

## 七、数据流与架构

### 7.1 优点

- **数据层分层清晰**: 内存 → 本地CSV → 网络，优先级明确
- **Observability 设计优秀**: 环形缓冲、timer 上下文管理器、线程安全
- **DelistedRegistry 设计巧妙**: TTL 重探机制（90天）避免永久误杀停牌复牌股
- **并发降级策略完善**: 50→20→10→5→串行的自适应降级

### 7.2 潜在问题

- 模块间耦合度较高，`fetcher.py` 中多处延迟导入是为避免循环依赖
- `data_layer.py` 与 `fetcher.py` 的职责边界不够清晰

---

## 优化优先级总表

| 优先级 | 问题 | 文件 | 预估工作量 | 状态 |
|---|---|---|---|---|
| **P0** | `_is_running` 竞态条件 | `core/server.py` | 小 | 🔄 已修复 |
| ~~P0~~ | ~~`api_screen` 超时判断逻辑~~ | ~~`core/server.py:222`~~ | ~~—~~ | ~~❌ 误判，逻辑正确~~ |
| **P1** | 策略 Boilerplate 重复（~6行共用前缀） | `strategies/*.py` + `base.py` | 中 | 待评估 |
| **P1** | `get_latest_trade_date` 返回非交易日（标签用途，低影响） | `data/fetcher.py:214` | 小 | 待评估 |
| **P1** | LRU 缓存近似 FIFO（dict 插入序） | `data/fetcher.py:461` | 小 | 待评估 |
| **P1** | 指标缓存无上限 | `data/fetcher.py:400` | 小 |
| **P2** | `merge_results` 重复计算指标 | `core/engine.py:771` | 中 |
| **P2** | `get_indicators` days 不一致 | `strategies/*.py` | 中 |
| **P2** | 策略串行执行 | `core/engine.py:547` | 大 |
| **P3** | 前端单文件过大 | `web/templates/index.html` | 大 |
| **P3** | 前端 tooltip 与后端不同源 | 前后端 | 中 |
| **P3** | 测试覆盖不足（缺 engine 测试） | `tests/` | 中 |
