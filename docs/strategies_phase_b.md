# 策略互补性矩阵（阶段 B）

> 目的：识别同源/高度重合的策略对，给出"合并 / 降权 / 保留"建议，输入到阶段 C 的单策略深度 review。
> 状态：**结构性分析 + 数据驱动 Jaccard 已完成**（2026-06-23 主板筛选结果回填）。

---

## 🔄 2026-06-23 真实 Jaccard 数据回填——阶段 B 预测大量翻车

跑完一轮全市场扫描（主板，elapsed 953.9s），用每个策略的 `all_hit_codes` 全集算两两 Jaccard，结论与预测严重不符。

### 真实命中数（修复 cached_count bug 后）

| 策略 | 真实命中 | vs 22 日 | 备注 |
|---|---|---|---|
| td_sequential | 300 ⚠️被截 | 300 | 阈值仍未补，依然失控 |
| rsi_oversold | 300 ⚠️被截 | 300 | 当前市场震荡，RSI 普遍偏低 |
| chanlun_strict | 300 ⚠️被截 | 300 | 65 阈值仍触发普遍 |
| right_side | 300 ⚠️被截 | 300 | 60 阈值过松 |
| macd_bull | 300 ⚠️被截 | 300 | 阈值 90 但触发面广 |
| rps_breakout | 172 | 227 | 强度+突破双门槛真实过滤 |
| strong_stock | 150 | 161 | 稳定 |
| bollinger_bands | 136 | 126 | 稳定 |
| volume_breakout | 29 | 29 | 严格双门槛真实过滤 |
| tail_market | 5 | 5 | 罕见信号 |
| high_tight_flag | 2 | 3 | 罕见形态 |

### Pairwise Jaccard 矩阵（全集）

```
                    bollinge chanlun_ macd_bul right_si rps_brea rsi_over strong_s td_seque volume_br
bollinger_bands     —          2.3%    3.3%    2.8%    2.7%    0.0%    3.6%    0.5%    0.0%
chanlun_strict      2.3%    —          6.6%    7.5%    4.0%    0.8%    3.2%    0.8%    1.5%
macd_bull           3.3%    6.6%    —         21.5%   23.6%    0.0%   17.5%    0.0%    5.4%
right_side          2.8%    7.5%   21.5%    —         22.0%    0.0%   29.3%    0.0%    8.9%
rps_breakout        2.7%    4.0%   23.6%   22.0%    —          0.0%   22.4%    0.0%    4.1%
rsi_oversold        0.0%    0.8%    0.0%    0.0%    0.0%    —          0.0%    3.6%    0.0%
strong_stock        3.6%    3.2%   17.5%   29.3%   22.4%    0.0%    —          0.0%   12.6%
td_sequential       0.5%    0.8%    0.0%    0.0%    0.0%    3.6%    0.0%    —          0.0%
volume_breakout     0.0%    1.5%    5.4%    8.9%    4.1%    0.0%   12.6%    0.0%    —
```

**没有任何策略对 Jaccard > 30%**。整个策略池比预测的更互补。

### 阶段 B 预测对照

| 预测簇 | 预测 J | 实测 J | 结论 |
|---|---|---|---|
| 底反族 td_seq × rsi_oversold | 30-50% | **3.6%** | ❌ 完全错。TD 看连续下跌结构，RSI 看绝对超卖水平，筛出几乎不重叠的票 |
| 底反族 td_seq × chanlun_strict | 30-50% | 0.8% | ❌ 完全错 |
| 突破量价 vol_break × rps_break | 60-80% | 4.1% | ❌ 完全错 |
| 突破量价 vol_break × right_side | ≥50% | 8.9% | ❌ 但有嵌套（见下） |
| 突破量价 rps_break × right_side | 50-70% | 22.0% | ⚠️ 部分对（中度但远低于预测） |
| 强势 strong × rps | 30-45% | 22.4% | ⚠️ 接近下沿但低于预测 |
| 趋势 macd × right_side | 30-40% | 21.5% | ⚠️ 接近下沿 |
| 形态独立 chanlun × * | <15% | <8% | ✅ 预测对，独立 |

### 真正的关系是嵌套，不是并行同源

虽然 Jaccard 都 < 30%，但用"重叠率（交集 / 较小集合）"看，存在显著的嵌套结构：

```
volume_breakout (29) ⊂ right_side (300):  29 票里 27 被 right_side 也选 → 93% 嵌套
strong_stock (150)   ⊂ right_side (300):  150 票里 102 被 right_side 也选 → 68% 嵌套
rps_breakout (172)   ⊂ macd_bull (300):   172 票里 90 被 macd_bull 也选  → 52% 嵌套
strong_stock (150)   ⊂ rps_breakout (172): 150 票里 59 被 rps 也选       → 39%
volume_breakout (29) ⊂ strong_stock (150): 29 票里 20 被 strong 也选     → 69%
```

**结论翻转**：
1. **`volume_breakout` 不是与 rps 同源，而是 `right_side` 的高分子集**（93%）。Jaccard 看着 8.9%，是因为分母里的 right_side 太大（300 vs 29），实际上 volume_breakout 提供独特候选只有 2 票
2. 阶段 B 优先级 1 的"volume_breakout 并入 rps_breakout"——错。应当并入 `right_side`，或者作为 right_side 的"高量比加分项"
3. **底反族真的互补**，不是同源——保留 td_sequential / rsi_oversold / chanlun_strict 全部三者，三者两两 Jaccard < 4%
4. **right_side × strong_stock = 29.3% 是当前最高对**（不在我预测里），都强调"趋势+量能"，需阶段 C 关注

### 突破量价族 Jaccard 远低于预测的根本原因

阶段 B 预测错在哪里：我预设这些策略"都强制要求创新高"，所以全集应当高度重合。但**没区分"新高的窗口长度"**：
- right_side: 突破近期高（动态窗口）
- volume_breakout: 突破 30 日（短窗）
- rps_breakout: 突破 60 日 + 强度过滤（中窗 + 强度门槛）
- macd_bull: 不强求新高，主要看 MA 多头

不同窗口长度选出的"创新高"票不一样——20 日突破的票未必能创 60 日新高，尤其在震荡市。这个细节在阶段 A 我有提（"窗口 20/30/60/120/250"），但阶段 B 推断 Jaccard 时没考虑窗口差异的过滤效应。

### precalc 复用率（API 返回）

```
[precalc 复用率] 策略期间 get_indicators 调用 52060 次
  命中 36442 / 未命中 15618 (70.0%)
  按 days 命中: {120: 36442}
  按 days 未命中: {120: 5206, 250: 5206, 80: 5206}
  当前缓存条目: 15618
```

**解读**：
- days=120 的 11 个策略里有 7 个完全 hit，但 1 个全 miss（5206 = 全市场，可能是 chanlun_strict 走 `get_history` 不算 hit；或是 pure_kline=True 的差异）
- days=250 (rps_breakout) 完全 miss → precalc 没帮上忙
- days=80 (tail_market) 完全 miss → 同上

**结论**：阶段 A 预测准确——precalc 命中率 70% 是被这两个非 120 策略拖低的；只看 days=120 的策略命中率约 87.5%，是有效的。修复路径：把 rps/tail_market 改成 days=120 简单粗暴最有效。

---

---

## 数据现状（2026-06-22 主板筛选）

| 策略 | hit_count | top_n | 阈值 | 关键门槛 |
|---|---|---|---|---|
| td_sequential | 300 ⚠️ | 20 | **无**（count=8 即 30 分入选） | TD count ∈ {8, 9} |
| rsi_oversold | 300 ⚠️ | 20 | 60 | RSI ≤ 60 |
| chanlun_strict | 300 ⚠️ | 20 | 65 | 底背驰 + 买点形态 |
| right_side | 300 ⚠️ | 20 | 60 | 趋势 + 突破（弱） |
| macd_bull | 300 ⚠️ | 20 | 90 | DIF>DEA + 多 MA 多头 |
| rps_breakout | 227 | 20 | 75 | 强度+>60 日新高 |
| strong_stock | 161 | 20 | 55 | 量能+涨幅 |
| bollinger_bands | 126 | 20 | 75 | 下轨/上轨触发 |
| volume_breakout | 29 | 20 | 85 | 量比≥2.0+突破 30 日 |
| tail_market | 5 | 5 | 多门槛 | 尾盘强势模式 |
| high_tight_flag | 3 | 3 | 多门槛 | 50%+ 急涨后紧整理 |

**5 个策略命中 = 300 (= top_n 上限)**。说明它们在主板 ~3500 票里的命中率大于 8.5%，要么是阈值过松、要么是当前市场环境共振。

---

## ⚠️ top-20 Jaccard 不可信的根因

我用现有 `last_screen_result.json` 的 `top_stocks`（每策略 20 只）算了 Jaccard，结果如下（仅显著值）：

```
volume_breakout × rps_breakout: 33.3% (交=10)
right_side × volume_breakout:   21.2% (交=7)
right_side × rps_breakout:      17.6% (交=6)
strong_stock × rps_breakout:    14.3% (交=5)
td_sequential × rsi_oversold:   0.0%   ← 严重失真
chanlun_strict × *:             ≤0.0%  ← 严重失真
```

**为什么失真**：5 个策略命中数都达到 300（= top_n 上限被截）。每个策略的 top-20 是按"自家打分排序"取前 20，分数高的票每个策略各不一样：
- `td_sequential` top 20 = 集中在 count=9 + 趋势好 + MACD 金叉那批
- `rsi_oversold` top 20 = 集中在 RSI 真的<30 那批
- 但它们的"全集 300"都是"近期下跌且接近低点"的票，全集重合度大概率 30-50%

**结论**：top-20 Jaccard 只能反映"两个策略最看好的票是否一致"，**不能反映两个策略选股池的整体相似度**。需要 `all_hit_codes` 全集重算。已经在 `core/engine.py:1048` 加好字段，等下次筛选产生数据。

---

## 基于源码的结构性同源分析（预测）

把 11 个策略按"决定命中的核心因子"分组，预测全集 Jaccard 量级。

### 🔴 强同源簇（预测 Jaccard ≥ 50%）

#### A. 突破 + 量价族
- **`volume_breakout`** （量比≥2.0 + 突破 30/60 日高，阈值 85）
- **`rps_breakout`** （多周期强度 + 突破 60 日高，阈值 75）
- **`right_side`** （趋势 + 突破，阈值 60 较松）

**论据**：三者都强制要求"创近期新高"作为 hard gate，价格站在 MA50/MA20 上方加分。差异仅在"新高窗口（30/60/120）"和"量比要求强度"。当市场有 leader 板块时，三者会聚合到同一批票。

**top-20 已观察到**：volume_breakout × rps_breakout = 33%（top-20 视角已不低），全集应达到 60-80%。

**建议**：
- `volume_breakout` (29 命中) 和 `rps_breakout` (227 命中) 是"严格版"和"宽松版"——相当于嵌套关系
- 阶段 C 优先合并：用 `rps_breakout` 作为基础（覆盖更广），`volume_breakout` 作为 boost 信号（量比≥2 加分）而非独立策略

#### B. 底部/超卖反转族
- **`td_sequential`** （TD count=8/9，价格连续下跌）
- **`rsi_oversold`** （RSI<60，超卖+背离）

**论据**：两者都筛"近期下跌中接近低点"的票。TD count 高意味着已经 8-9 天每日 close < close[-4]，这种走势 RSI 大概率 < 50。它们的 hard gate 不重叠（TD vs RSI），但底层选股逻辑高度相关。

**当前异常**：top-20 Jaccard = 0%。这说明两个 300 命中里，**td_sequential 把分数最高的 20 个给了"count=9 + 趋势恢复"的票，rsi_oversold 把分数最高的 20 个给了"真 RSI<30"的票**——它们的 top 不一样，但全集应当显著重叠。

**修复优先**：`td_sequential` 没有 score 阈值（[[strategy-audit-findings]] 4 必修 bug 之一），命中 300 是被 top_n 截了，不是真有 300 个有效信号。补 `score >= 50` 后命中数会大幅下降，此对的实际重要性才看得清楚。

### 🟡 中度同源簇（预测 30% ≤ Jaccard < 50%）

#### C. 强势/动量族
- **`strong_stock`** （量能+涨幅+阳线密度，阈值 55 偏松）
- **`rps_breakout`**（亦在 A 簇）
- ~~`momentum`~~（本次未跑，但典型"近期涨幅+量能"，与 strong_stock 高度同质）

**论据**：top-20 已观察到 strong_stock × rps_breakout = 14%，全集预测 30-45%。两者都偏好"近期涨得多+量能强"，但 strong_stock 不强制创新高，能选到"量能扎实但还没突破"的票，差异化保留价值大。

**建议**：保留两者，互为补充（rps_breakout = 已突破，strong_stock = 蓄势待发）。

#### D. 趋势确认族
- **`macd_bull`**（DIF>DEA + 多 MA 多头，阈值 90 严格）
- **`right_side`**（趋势 + 突破，阈值 60 偏松）

**论据**：都要"价格在 MA20/MA60 上方"。`macd_bull` 阈值 90 + 严格多头排列，命中本来应当稀少，但本次也到了 300 —— 说明 90 分容易达到 (10+ 个+10 加分项叠加都能过)，这是阶段 C 的检查点。

**预测**：top-20 Jaccard 可能仍低（macd_bull 偏好稳健大票，right_side 偏好刚启动），全集 30-40%。

### 🟢 弱相关 / 真独立

#### E. 形态学族
- **`chanlun_strict`** —— 唯一走 `get_history` 自算 MACD 的策略，逻辑是缠论买点+背驰，与其他全部走 `get_indicators` 的策略选股逻辑正交
- **`high_tight_flag`** —— 50%+ 急涨后紧整理，极罕见形态，本次 3 命中

**预测**：chanlun_strict 与所有其他策略 Jaccard < 15%，是真独立。high_tight_flag 数据太少，等市场出现典型形态再评估。

#### F. 双模式叠加（设计可疑）
- **`bollinger_bands`** —— 同时跑"下轨反弹"和"上轨突破"两套打分，叠加到 145 后 min(100)

**论据**：与 A 簇（突破派）和 B 簇（反转派）都会部分重合，因为它自己就是两套语义并发。预测 Jaccard 都在 15-30% 区间，"看似互补但其实是因为内部混合"。

**阶段 C 必须处理**：拆成 `bollinger_lower` 和 `bollinger_upper` 两个独立策略，否则它在合并打分里会"占两个名额却不提供差异化"。

---

## 行动清单（数据修正版）

> ⚠️ 本节原是**基于结构推断的预测清单**，2026-06-23 真实 Jaccard 回填后大量翻车
> （见本文档顶部）。下面是按**真实命中全集**修正后的版本。

### 判据说明（重要）
到目前为止，所有"合并/保留"建议都基于**重叠度**（谁和谁选同样的票：Jaccard +
嵌套率），**没有一项用过回测 alpha/收益**。也就是"该砍谁"只回答了"谁冗余"，
没回答"谁赚钱"。下一步若要做实质合并，应补单策略含成本回测，按 α 而非仅按重叠决策。

### 优先级 1：先修 bug —— ✅ 全部完成
1. ✅ 修 `td_sequential` 阈值（已补 `score < 50` 淘汰）
2. ✅ 删 `chan20.py`（死代码已删）
3. ✅ 修 `limit_up_gene` 涨停判定（已 v2 重写，按板块阈值 + 真封板 close==high）
4. ✅ 删 `td_sequential` / `rsi_oversold` 末尾静默 except

### 优先级 2：合并候选 —— 两次翻转，最终**不合并**
- ❌ ~~`volume_breakout` 并入 `rps_breakout`~~（第一次翻转）：两者 Jaccard 仅 4.1%，不同源。
- ❌ ~~`volume_breakout` 并入 `right_side`~~（第二次翻转，**回测推翻重叠结论**）：
  单日主板快照里 volume_breakout 是 right_side 的"93% 子集、仅 2 票独特"，看似该并入。
  但那是 **right_side 收紧阈值前**的假象——当天 right_side 命中 300（被 top_n 截顶），
  自然吞掉 volume_breakout 的 29 票。right_side 阈值收紧到 80 后，**52 周回测**真实画像是：

  | 策略 | 笔数 | α(2日) | α(5日) | α(10日) | α(30日) |
  |---|---|---|---|---|---|
  | volume_breakout | 459 | **+0.89%** | **+1.50%** | +1.31% | +3.42% |
  | right_side | 260 | +0.58% | +0.88% | +1.27% | **+6.28%** |

  两者**收益画像互补**：volume_breakout = 短打（2/5 日 α 更高、笔数近 2 倍），
  right_side = 长持（30 日 α 远高）。合并会砍掉 459 笔真实短周期 α。**结论：保留两者。**
- [ ] `bollinger_bands` 拆双模式（`bollinger_lower` / `bollinger_upper`）——
  仍**只是代码逻辑论证**（单策略内含两套相反形态），无重叠或回测数据支撑，待验证。
- 数据新发现：`right_side × strong_stock = 29.3%` 是实测最高重叠对（不在原预测里）——
  但**别再只看重叠**，先按上表的方法对比两者回测 α 再决定，重蹈 volume_breakout 覆辙。

### 教训（本节核心）
volume_breakout 的合并建议被推翻两次，根因都是**用错了判据**：
单日同市场的 Jaccard/嵌套率会被某个策略的阈值松紧严重扭曲（截顶时吞噬一切），
**只有跨期含成本回测的 α 才是合并/降权的可靠判据**。重叠度只能用来"提名嫌疑对"，
不能用来"定罪"。

### 优先级 3：阶段 C 单策略深度 review 顺序
1. `right_side`（先吃下 volume_breakout 的子集，再评估与 strong_stock 的 29.3% 重叠）
2. `chanlun_strict`（840 行，性能差，单独 1-2 轮）
3. `right_side` / `macd_bull`（评估 D 簇 21.5% 是否值得保留两个）
4. `bollinger_bands`（拆双模式）
5. 其余
- 底反族（`td_sequential` / `rsi_oversold` / `chanlun_strict`）**全保留**：
  数据证明三者两两 Jaccard < 4%，真互补，不再是合并候选。

---

## 阶段 B 收尾 —— ✅ 已完成（2026-06-23）

- [x] 跑全市场"只筛选"，拿到各策略 `all_hit_codes` 全集
- [x] 全集两两 Jaccard 已算（见顶部矩阵），四簇预测大量翻车、已记录
- [x] precalc 复用率 70.0%（见上文），决定保留
- [x] 真实 Jaccard 矩阵已回填本文档顶部

已进入阶段 C。
