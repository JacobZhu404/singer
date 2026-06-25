# 策略索引表（阶段 A 产出）

> 目的：把 `strategies/` 下全部策略归纳成一张可对照的全景表，为后续互补性矩阵（阶段 B）和单策略深度 review（阶段 C）做基线。
> 数据基准：2026-06-22 时点的代码现状（branch: `refactor/serial-strategies-parallel-stocks`）。

---

## 全策略一览（按文件名字母序）

| 策略 ID | 中文名 | 因子分类 | days | score 阈值 | base_win_rate | 注册 | 行数 |
|---|---|---|---|---|---|---|---|
| `bollinger_bands` | 布林反弹/收口突破 | 反转+突破 | 120 | 75 | 0.55 | ✅ | 149 |
| `chan20` | 缠20（MACD底二次金叉+SKDJ） | 缠论+反转 | 120 | 55 | 0.55 | ❌ **死代码** | 163 |
| `chanlun_strict` | 缠论严格版 | 缠论+形态 | 120 | 65 + 买点/背驰 | 0.60 | ✅ | 840 |
| `golden_cross` | 均线金叉宽松版 | 趋势 | 60 | 65 | 0.52 | ✅ | 137 |
| `high_tight_flag` | 高紧旗形 | 形态+突破 | 120 | 80 | 0.55 | ✅ | 177 |
| `limit_up_gene` | 涨停基因 | 动量+量价 | 120 | **40**（全策略最低） | **0.65**（最高） | ✅ | 121 |
| `macd_bull` | MACD 多头排列 | 趋势 | 120 | **90**（最高） | 0.60 | ✅ | 199 |
| `momentum` | 横截面动量 | 动量 | 120 | 85 | 0.58 | ✅ | 162 |
| `right_side` | 右侧交易 | 突破+趋势 | 120 | 60 | 0.58 | ✅ | 186 |
| `rps_breakout` | RPS 相对强度突破 | 横截面RS+突破 | **250**（最长） | 75 + breakout | 0.58 | ✅ | 163 |
| `rsi_oversold` | RSI 超卖反转 | 反转 | 120 | 60 | 0.58 | ✅ | 126 |
| `strong_stock` | 强势股 | 量价+动量 | 120 | 55 | 0.62 | ✅ | 142 |
| `tail_market` | 尾盘强势日线近似 | 量价+趋势 | 80 | 70 | **0.54**（最低之一） | ✅ | 182 |
| `td_sequential` | 神奇九转 | 反转+形态 | 120 | **无显式**（≈30） | 0.58 | ✅ | 106 |
| `volume_breakout` | 量价突破 | 突破+量价 | 120 | 85 | 0.58 | ✅ | 239 |

**统计**：15 个策略文件，14 个在册（chan20 未注册），代码约 3300 行（不含 base/registry）。

---

## 高优先级发现（直接影响生产）

### 🔴 必须修复
1. **`chan20.py` 是死代码** — 未在 `registry.py` 注册，但每次 `precalc` 都会跟着扫整文件（实际上没被调起，只是占代码维护成本）。要么补注册，要么直接删除。
2. **`td_sequential` 没有 score 下限** — `count=8` 的"预警"就 30 分入选，与其它策略阈值 60-90 的风格完全不一致，会造成命中数失控、被 base 排名稀释。建议补 `score >= 50` 阈值。
3. **`limit_up_gene` 涨停判定错误** — 用 `pct >= 9.5%` 判主板涨停，但科创板/创业板涨停是 20%、ST 是 5%，全部误判。应改用 close 与前日 close 的 `(price_limit_factor)` 计算。
4. **`rsi_oversold.py` / `td_sequential.py` 末尾静默吞错** — `except Exception: logger.debug` 会掩盖 bug，且与 `base.screen` 的 `_SkipStock` 机制重复，建议删除（base 已经统一处理）。

### 🟡 同质化严重（阶段 B 重点验证）
- **突破派四件套**：`right_side` / `volume_breakout` / `rps_breakout` / `high_tight_flag` 骨架都是「创近期新高 + 放量 + 趋势确认」，区别仅在新高窗口（20/30/60/120/250）和加分项细节。预测两两 Jaccard 会很高。
- **均线金叉对**：`macd_bull` / `golden_cross` 都要"三/四线多头"，golden_cross 自称"更早入场"但仍卡三线多头，实际并不更早。
- **动量三胞胎**：`momentum` / `rps_breakout` / `strong_stock` 都靠"近期涨幅 + 量能"，差别只在权重和是否要求创新高。
- **底部反转三兄弟**：`chan20` / `td_sequential` / `rsi_oversold` 都做底部反转，分别用 MACD+SKDJ / TD count / RSI<30 触发。
- **重叠预测**：阶段 B 的 backtest 结果如果在上述对子上 Jaccard > 60%，建议合并或降权重；否则差异化是真的，可以保留。

### 🟢 设计可疑但影响小
1. **`bollinger_bands` 双模式叠加** — 同一只票既触下轨又突破上轨时分数会被叠加到 145 再 min(100)，两种语义完全相反却同分加和。建议拆成两个独立策略，或互斥取最大。
2. **`right_side` 给"未突破"也加 5 分基础分** — 违背"右侧交易"语义，且阈值才 60 偏松，命中量会比设计预期多很多。
3. **`chanlun_strict._ema` 是纯 Python loop** — 全市场 5000+ 只跑下来会慢，应改为 `pd.Series.ewm()` 或调 `utils.indicators.calc_macd`。

---

## 重复造轮子清单（可抽 helper）

| 重复逻辑 | 出现位置 | 建议 |
|---|---|---|
| `_get_indicators(days=N) + kline 长度兜底 + SkipStock` 模板 | 全部 15 个策略开头 | 抽 decorator `@require_indicators(days=120, min_bars=N)` |
| "突破近 N 日新高 + breakout_pct 计算" | right_side / volume_breakout / rps_breakout / tail_market / high_tight_flag | `utils.signals.breakout_n_day(kline, n)` |
| "四线/三线多头判定" | macd_bull / golden_cross / tail_market / right_side | `utils.signals.is_ma_bull(mas, levels=[5,10,20,60])` |
| "放量加分梯度 1.5/2.0/3.0 → +X" | ≥5 处 | `utils.signals.volume_grade_score(vr)` |
| "MA60 趋势过滤" | bollinger / rsi 等 | 同上 helper |
| MA20 / MA50 重算（不复用 indicators 缓存） | momentum / rps_breakout / volume_breakout | 改为读 `indicators["ma"]` |
| chanlun 自实现 MACD | chanlun_strict.py | 改用 `utils.indicators.calc_macd` |

> 这些重构与阶段 B/C 的策略评估**不要混做**——先完成"哪些策略值得留下"，再为留下来的策略做共享 helper。否则给即将砍掉的策略做重构纯属浪费。

---

## 数据依赖一致性

`precalc` 默认 `days=120`，与所有策略对齐情况：

- ✅ **120 匹配**：bollinger_bands / chan20 / chanlun_strict（自走 get_history） / high_tight_flag / limit_up_gene / macd_bull / momentum / right_side / rsi_oversold / strong_stock / td_sequential / volume_breakout
- ⚠️ **不匹配**：
  - `golden_cross` days=60 → 与 precalc 的 `_120_False` cache key 不同 → 每次重算
  - `tail_market` days=80 → 同上
  - `rps_breakout` days=250 → 同上，且是反过来：precalc 算了 120，rps 还要再补 130 天
  - `chanlun_strict` 走 `get_history` 不走 `get_indicators` → precalc 完全帮不上忙

**预测**：等会儿你跑完那轮筛选，`[precalc 复用率]` 日志里会看到：
- `hit_by_days: {120: ~N×11}` 大头
- `miss_by_days: {60: ~N, 80: ~N, 250: ~N}` 是这三个非 120 的策略
- chanlun_strict 完全不计入 indicator stats（它不走 get_indicators）

如果总命中率 < 50%，主因就是这几个 days 不一致。修复路径任选其一：
1. 把上述策略统一到 days=120（最简单，但 rps 会损失数据）
2. precalc 做多档（120 + 250），key 拓展为 `_{days}_`
3. 砍 precalc，让各策略按需取（如果命中率本来就低，这是最干净的方案）

---

## 与公开范式对照

| 策略 | 公开范式 / 论文 |
|---|---|
| `bollinger_bands` | John Bollinger - Bollinger Band Squeeze |
| `chan20` / `chanlun_strict` | 缠中说禅理论（A 股原生） |
| `golden_cross` / `macd_bull` | 经典双/多均线 + Appel MACD |
| `high_tight_flag` | 欧奈尔 CANSLIM 形态学 |
| `limit_up_gene` | A 股游资连板模式（无国际对应） |
| `momentum` | Jegadeesh-Titman (1993) 横截面动量 |
| `right_side` / `volume_breakout` | 唐奇安通道突破（海龟） + Granville 量价 |
| `rps_breakout` | 欧奈尔 RPS (CANSLIM-L) |
| `rsi_oversold` | Wilder RSI 反转 + 看涨背离 |
| `strong_stock` | A 股短线放量连阳（散户体系）+ Jegadeesh 52w high |
| `tail_market` | A 股"14:30 尾盘选股法"（散户传统） |
| `td_sequential` | Tom DeMark TD Sequential |

**观察**：项目内只有 `momentum` / `rps_breakout` 是真正可追溯到学术论文的因子；其余多数是 A 股散户传统或 TA 经典，从「101 Alphas」框架看属于「formulaic alpha」族但相互独立性需要在阶段 B 量化验证。

---

## 待办（阶段 B、C）

- [x] 阶段 B：全市场扫描跑两两 Jaccard 矩阵（结论：**无任何一对 >30%**，比预测互补；
  见 `strategies_phase_b.md` 顶部数据回填）
- [x] 阶段 C 优先级 1（bug 修复）：td_sequential 阈值 / chan20 死代码 / limit_up_gene
  涨停判定，**均已完成**
- [ ] 阶段 C 剩余（按数据修正后的 ROI）：
  1. ~~`right_side` 吃下 `volume_breakout`~~ —— **回测否决**：两者 52 周 α 画像互补
     （vb=短打、right=30 日），93% 嵌套是 right_side 收紧阈值前的截顶假象，保留两者
  2. `chanlun_strict`（最复杂、性能最差、最值得精化）
  3. `bollinger_bands` 拆双模式（待数据支撑）
  4. 其它
  - **判据教训**：合并/降权只能用跨期含成本回测 α 定夺；单日重叠度会被阈值松紧扭曲，
    只能用来提名嫌疑对，不能定罪（详见 `strategies_phase_b.md`「教训」节）
