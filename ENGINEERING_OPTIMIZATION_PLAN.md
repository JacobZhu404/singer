# 歌者智能选股系统 — 工程优化计划

**版本**: v1.0  
**日期**: 2026-05-18  
**依据**: 腾讯自选股圆桌报告（6位专家独立评估）  

---

## 一、当前工程架构速览

```
stock_screener/
├── data/                    # 数据层（新浪/腾讯/东财，无基本面数据）
│   ├── data_sources.py      # 多源适配器
│   ├── fetcher.py           # 统一数据接口
│   └── local_cache.py       # 本地K线缓存
├── strategies/              # 策略层（11个纯技术面策略）
│   ├── base.py              # BaseStrategy + StockSignal
│   ├── registry.py          # 策略注册表
│   └── [11个策略文件].py
├── core/
│   ├── engine.py            # ScreenEngine（调度/合并/风险扫描）
│   └── server.py            # Flask REST API + SSE
├── utils/
│   ├── indicators.py        # 技术指标计算
│   ├── sell_signals.py      # 卖出信号 + 买入风险评估
│   └── market_trend.py      # 大盘趋势判断
├── portfolio/               # 持仓管理
└── web/templates/index.html # 前端单页应用
```

### 已有能力
- K线数据多源获取 + 本地缓存
- 11策略并行扫描 + 串行调度
- 卖出信号检测 + 买入风险评估
- 综合推荐合并（加权评分 + 风险调整）
- 大盘趋势过滤（bull/bear/neutral）
- 持仓管理 + 虚拟交易

### 核心缺失
- **无基本面数据**（PE、ROE、营收增速、净利润增速）
- **策略信号无止损价**（止损只在合并层出现）
- **无策略方向分类**（追高/抄底无标识）
- **已知代码缺陷**（momentum描述欺诈、limit_up_gene扫描范围异常）

---

## 二、差距分析对照表

| 专家建议 | 工程现状 | 差距等级 |
|---------|---------|---------|
| 加入估值过滤层（PE/PEG/PE Bands） | 数据层无基本面接口 | 🔴 架构级缺失 |
| 加入基本面过滤（ROE>5%、营收增速>0） | StockSignal无基本面字段 | 🔴 架构级缺失 |
| 所有策略加入明确止损规则 | sell_signals.py已有stop_loss计算，但策略信号未填充 | 🟡 实现不完整 |
| 修复momentum"排名前10%"描述 | description写排名，代码无排名逻辑 | 🔴 假描述 |
| 修复limit_up_gene扫描范围 | _get_codes重写为仅扫涨停列表（最多200只） | 🔴 行为异常 |
| 删除/标注抄底策略 | registry中无方向分类 | 🟡 元数据缺失 |
| 整合重复策略（11→4模块） | 11策略平铺注册，无模块分组 | 🟢 低优先级 |
| 板块强度确认 | 无板块/行业数据 | 🟢 低优先级 |
| 仓位管理（牛熊→仓位） | market_trend已有趋势判断，无仓位模块 | 🟢 低优先级 |
| 热点轮动识别 | 无新闻/热点数据源 | 🟢 低优先级 |

---

## 三、分阶段实施计划

---

### 第一阶段：止血修复（优先级：P0，预计 1-2 天）

目标：消除已确认的代码缺陷和误导性描述，不引入新依赖。

#### 步骤 1.1 — 修复 momentum.py 描述欺诈

**文件**: `strategies/momentum.py`  
**问题**: `description = "价格动量排名前10%+量能确认"`，但代码只计算单股涨幅，从未与全市场排名。  
**动作**:
```python
# 修改前
description = "价格动量排名前10%+量能确认，捕捉趋势延续"
# 修改后
description = "价格动量强度+量能确认，捕捉趋势延续"
# 并在策略注释中注明：排名逻辑暂由引擎层评分替代
```
**验收**: description 不再包含"排名前10%"字样。

---

#### 步骤 1.2 — 修复 volume_breakout.py 负数索引风险

**文件**: `strategies/volume_breakout.py`  
**问题**: 第146行 `for j in range(-10, -1)` 中使用 `high.iloc[j-20:j]`，当 `j=-10` 时 `j-20=-30`，`iloc[-30:-10]` 在数据不足30根时返回空序列，`max()` 会抛 ValueError。  
**动作**:
```python
# 优化3: 加入突破后回踩确认
if len(close) >= 10 and has_breakout:
    for j in range(len(close) - 10, len(close) - 1):  # 改为正数索引
        if j < 20:
            continue
        window_high = high.iloc[j-20:j]
        if window_high.empty:
            continue
        if high.iloc[j] > window_high.max():
            ma10_at_j = close.rolling(10).mean().iloc[j]
            if (close.iloc[-1] > close.iloc[j] and
                    min(close.iloc[j+1:len(close)].tolist()) < ma10_at_j * 1.02):
                signals.append("突破后回踩确认")
                score += 20
                break
```
**验收**: 回测 `volume_breakout` 策略不抛异常。

---

#### 步骤 1.3 — 修复 limit_up_gene.py 扫描范围异常

**文件**: `strategies/limit_up_gene.py`  
**问题**: `_get_codes` 重写为只从当日涨停列表获取（最多200只）。如果当日无涨停数据或接口异常，策略扫描0只股票，与其他策略扫描全市场的逻辑不一致。  
**动作**:
```python
def _get_codes(self, stock_list: pd.DataFrame) -> List[str]:
    # 优先从涨停列表获取，但保留全市场 fallback
    from ..data.fetcher import get_limit_list, get_latest_trade_date
    trade_date = get_latest_trade_date()
    try:
        limit_df = get_limit_list(trade_date)
        if not limit_df.empty:
            code_col = next((c for c in ["symbol", "code", "代码", "ts_code"]
                             if c in limit_df.columns), None)
            if code_col:
                limit_df = limit_df.copy()
                limit_df["ts_code"] = limit_df[code_col].astype(str).str.zfill(6)
                codes = limit_df["ts_code"].tolist()
                if codes:
                    return codes[:200]
    except Exception as e:
        logger.warning(f"涨停列表获取失败，回退到全市场: {e}")
    # fallback: 使用基类默认逻辑（全市场）
    return super()._get_codes(stock_list)
```
**验收**: 涨停接口异常时，策略仍能扫描全市场。

---

#### 步骤 1.4 — 清理胜率计算残留

**文件**: `strategies/base.py`, `strategies/momentum.py`, `core/engine.py`  
**背景**: 前期已移除 merge_results 中的胜率排序和 API 返回，但策略内部仍在计算和填充 `win_rate`。  
**动作**:
1. `base.py` 第154-160行：保留 `_calc_win_rate` 方法（供历史兼容），但 `StockSignal` 中 `win_rate` 字段设为可选默认值 `None`
2. `base.py` 第26行：
   ```python
   win_rate: Optional[float] = None   # 原: float
   ```
3. 各策略文件：不再调用 `self._calc_win_rate()`，改传 `win_rate=None`
4. `engine.py` 第395行：移除 `win_rate_weight` 对 `weighted_score` 的影响
   ```python
   # 删除此行
   win_rate_weight = 1.0 + (base_win_rate - 0.5) * 0.8
   # 改为
   entry["weighted_score"] += sig.score * weight + rank_bonus
   ```
**验收**: 全局搜索 `win_rate` 确认策略层不再主动计算胜率。

---

### 第二阶段：止损规则完善（优先级：P1，预计 2-3 天）

目标：让每个买入信号都携带明确的止损价，前端可展示。

#### 步骤 2.1 — BaseStrategy 统一计算止损价

**文件**: `strategies/base.py`  
**现状**: `utils/sell_signals.py` 的 `detect_sell_signals` 已返回 `stop_loss_price`（近期低点×0.97），但策略层未调用。  
**动作**: 在 `BaseStrategy` 中增加辅助方法，供子类在构造 `StockSignal` 时调用：
```python
def _calc_stop_loss(self, df: pd.DataFrame, entry_price: float) -> float:
    """基于近期K线计算建议止损价"""
    if df is None or len(df) < 10:
        return round(entry_price * 0.95, 2)  # 默认5%止损
    close = df["close"].astype(float)
    low = df["low"].astype(float)
    # 近期低点下方3%，或入场价的-5%，取较高者（ tighter stop）
    recent_low = low.iloc[-20:].min() if len(low) >= 20 else low.min()
    stop_from_low = round(float(recent_low) * 0.97, 2)
    stop_from_entry = round(entry_price * 0.95, 2)
    return max(stop_from_low, stop_from_entry)

def _calc_take_profit(self, df: pd.DataFrame, entry_price: float) -> float:
    """基于近期K线计算建议止盈价"""
    if df is None or len(df) < 10:
        return round(entry_price * 1.08, 2)
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    recent_high = high.iloc[-20:].max() if len(high) >= 20 else high.max()
    take_from_high = round(float(recent_high) * 1.03, 2)
    take_from_entry = round(entry_price * 1.08, 2)
    return max(take_from_high, take_from_entry)
```
**验收**: 所有策略信号对象均可通过 `sig.extra.get("stop_loss_price")` 获取止损价。

---

#### 步骤 2.2 — 各策略填充止损/止盈价

**文件**: `strategies/momentum.py`, `strategies/strong_stock.py`, `strategies/right_side.py`, `strategies/volume_breakout.py`, `strategies/macd_bull.py`, `strategies/golden_cross.py`, `strategies/chanlun_strict.py`, `strategies/bollinger_bands.py`, `strategies/rsi_oversold.py`, `strategies/td_sequential.py`, `strategies/limit_up_gene.py`  
**动作**: 在每个策略构造 `StockSignal` 时，调用 `_calc_stop_loss` / `_calc_take_profit` 并写入 `extra`：
```python
entry_price = float(price_now)  # 或当前收盘价
stop_loss = self._calc_stop_loss(df, entry_price)
take_profit = self._calc_take_profit(df, entry_price)

# 写入 extra
extra={
    "stop_loss_price": stop_loss,
    "take_profit_price": take_profit,
    # ... 其他原有字段
}
```
**验收**: 通过 `/api/quick_screen` 或 `/api/result` 检查返回的 `stocks[].extra.stop_loss_price` 不为空。

---

#### 步骤 2.3 — 前端展示止损/止盈

**文件**: `web/templates/index.html`  
**动作**:
1. 在单策略结果表格中新增两列："止损价"、"止盈价"
2. 在综合推荐卡片中展示："建议止损: ¥X.XX (-X%)"
3. 持仓管理页面中，对比当前价与止损价，当前价跌破止损价时标红警告
**验收**: 前端可见每只推荐股票的止损/止盈价格。

---

### 第三阶段：引入基本面过滤（优先级：P2，预计 1-2 周）

目标：接入基本面数据，建立轻量级估值过滤层。

#### 步骤 3.1 — 扩展数据层获取基本面数据

**文件**: `data/data_sources.py`, `data/fetcher.py`  
**动作**:
1. 在 `data_sources.py` 中新增 `FundamentalDataSource` 类，使用 akshare 免费接口：
   ```python
   # akshare.stock_financial_report_indicator 或
   # akshare.stock_a_indicator_lg (理杏仁，含PE/PB/ROE)
   ```
2. 在 `fetcher.py` 中新增：
   ```python
   def get_stock_fundamental(codes: List[str]) -> pd.DataFrame:
       """获取股票基本面数据，返回 DataFrame[ts_code, pe, pb, roe, revenue_growth, profit_growth]"""
   ```
3. 增加本地缓存：`local_cache.py` 缓存基本面数据（每日更新一次即可）
**验收**: 能成功拉取全市场股票的PE、ROE、营收增速数据。

---

#### 步骤 3.2 — BaseStrategy 增加基本面过滤钩子

**文件**: `strategies/base.py`  
**动作**:
1. `StockSignal` 增加基本面字段：
   ```python
   pe: Optional[float] = None
   roe: Optional[float] = None
   revenue_growth: Optional[float] = None
   profit_growth: Optional[float] = None
   ```
2. `BaseStrategy` 增加 `fundamental_filter` 方法：
   ```python
   def fundamental_filter(self, code: str, fundamental: dict) -> tuple:
       """
       基本面过滤，返回 (pass: bool, reasons: List[str])
       子类可覆盖以调整过滤条件
       """
       pe = fundamental.get("pe")
       roe = fundamental.get("roe")
       rev_growth = fundamental.get("revenue_growth")
       profit_growth = fundamental.get("profit_growth")

       reasons = []
       if pe is not None and (pe <= 0 or pe > 100):
           reasons.append(f"PE异常({pe:.1f})")
           return False, reasons
       if roe is not None and roe < 5:
           reasons.append(f"ROE过低({roe:.1f}%)")
           return False, reasons
       if rev_growth is not None and rev_growth <= 0:
           reasons.append(f"营收增速非正({rev_growth:.1f}%)")
           return False, reasons
       if profit_growth is not None and profit_growth <= 0:
           reasons.append(f"净利润增速非正({profit_growth:.1f}%)")
           return False, reasons
       return True, []
   ```
3. `BaseStrategy.screen()` 在调用 `_evaluate_single_stock` 之前，先执行基本面过滤：
   ```python
   # 预加载全市场基本面数据
   fundamentals = self._load_fundamentals(codes)
   # 在 _eval_one 中传入
   ```
**验收**: 高PE垃圾股被策略过滤掉，日志输出被过滤原因。

---

#### 步骤 3.3 — 高风险策略加强基本面过滤

**文件**: `strategies/limit_up_gene.py`, `strategies/momentum.py`, `strategies/volume_breakout.py`  
**背景**: 钊审财指出这3个策略最容易选到垃圾股。  
**动作**: 覆盖 `fundamental_filter`，使用更严格的条件：
```python
# limit_up_gene.py / momentum.py / volume_breakout.py
def fundamental_filter(self, code, fundamental):
    passed, reasons = super().fundamental_filter(code, fundamental)
    if not passed:
        return False, reasons
    # 额外条件
    goodwill_ratio = fundamental.get("goodwill_to_net_asset")
    if goodwill_ratio is not None and goodwill_ratio > 30:
        return False, [f"商誉占比过高({goodwill_ratio:.1f}%)"]
    return True, []
```
**验收**: 涨停基因策略不再推荐基本面恶化的股票。

---

#### 步骤 3.4 — 前端展示基本面数据

**文件**: `web/templates/index.html`  
**动作**:
1. 结果表格新增列：PE、ROE、净利润增速
2. PE > 50 时标黄，PE > 100 时标红
3. ROE < 5% 时标红
4. 综合推荐卡片中增加"估值标签"：
   - "低估" (PE < 20, ROE > 15)
   - "合理" (20 <= PE < 50)
   - "偏高" (PE >= 50)
**验收**: 前端可见每只股票的PE/ROE/增速。

---

### 第四阶段：策略分类与整合（优先级：P3，预计 3-5 天）

目标：让策略体系更清晰，便于用户理解和选择。

#### 步骤 4.1 — Registry 增加策略方向标签

**文件**: `strategies/registry.py`  
**动作**:
```python
STRATEGY_REGISTRY = {
    "macd_bull": {
        "cls": MACDBullStrategy,
        "name": "MACD多头排列",
        "direction": "trend_follow",   # 追高/顺势
        "risk_level": "medium",        # 估值风险等级
        # ...
    },
    "td_sequential": {
        "cls": TDSequentialStrategy,
        "name": "神奇九转",
        "direction": "bottom_fishing", # 抄底
        "risk_level": "low",
        # ...
    },
    # ... 其他策略
}
```
方向枚举：`trend_follow`（顺势/追高）、`bottom_fishing`（抄底）、`mixed`（混合）。  
风险等级枚举：`low`、`medium`、`high`（对应文衡价的评分）。
**验收**: `/api/strategies` 返回中包含 `direction` 和 `risk_level` 字段。

---

#### 步骤 4.2 — 前端标识策略类型

**文件**: `web/templates/index.html`  
**动作**:
1. 策略卡片上增加方向标识：
   - 顺势策略 → 绿色 "顺势" 标签
   - 抄底策略 → 蓝色 "抄底" 标签
2. 策略卡片上增加风险等级：
   - 低风险 → "🟢"
   - 中风险 → "🟡"
   - 高风险 → "🔴"
3. 策略选择面板增加分组：
   - "顺势策略"组
   - "抄底策略"组
   - "其他"组
**验收**: 前端用户一眼可辨策略方向。

---

#### 步骤 4.3 — 整合重复策略（可选）

**文件**: `strategies/registry.py`, `core/engine.py`  
**背景**: 星望远建议11→4模块整合。  
**动作**（轻量级方案，不删策略，只加模块分组）：
```python
STRATEGY_MODULES = {
    "trend_track": {
        "name": "趋势跟踪模块",
        "strategies": ["golden_cross", "macd_bull", "right_side", "momentum"],
        "description": "基于趋势确认和突破动能的顺势策略组",
    },
    "strong_stock_module": {
        "name": "强势股模块",
        "strategies": ["strong_stock", "volume_breakout"],
        "description": "基于量能和强势特征的短线策略组",
    },
    "chanlun_module": {
        "name": "缠论模块",
        "strategies": ["chanlun_strict"],
        "description": "基于中枢、背驰、三类买点的严格缠论策略",
    },
    "bottom_fishing_module": {
        "name": "超跌反弹模块",
        "strategies": ["td_sequential", "rsi_oversold", "bollinger_bands"],
        "description": "基于超卖和均值回归的左侧策略组",
    },
}
```
前端策略选择面板可按模块一键勾选。
**验收**: 用户可一键选择"趋势跟踪模块"自动勾选4个策略。

---

### 第五阶段：中低优先级项（优先级：P4，按需）

#### 步骤 5.1 — 仓位管理模块

**文件**: 新增 `portfolio/position_sizer.py`  
**动作**:
```python
def suggest_position(market_trend: str, market_strength: float,
                     stock_risk_level: str) -> float:
    """根据大盘趋势和个股风险建议仓位比例"""
    base = {"bull": 0.8, "neutral": 0.5, "bear": 0.2}.get(market_trend, 0.5)
    # 根据趋势强度微调
    adjustment = market_strength * 0.2
    # 个股风险调整
    risk_adj = {"low": 0.1, "medium": 0, "high": -0.2}.get(stock_risk_level, 0)
    return max(0.1, min(1.0, base + adjustment + risk_adj))
```
在 `engine.merge_results()` 中为每只股票增加 `suggested_position` 字段。  
前端买入弹窗中展示建议仓位。

---

#### 步骤 5.2 — 板块强度确认

**文件**: `data/data_sources.py`  
**动作**: 新增接口获取板块涨停数量和封单量（需新数据源，如东财行业板块接口）。  
在 `merge_results()` 中增加板块强度加分项。

---

#### 步骤 5.3 — 建立"基本面股票池"

**文件**: `core/engine.py`  
**动作**: 在 `get_recommendation()` 的第一步，先用基本面过滤全市场，生成"健康池"，后续所有策略只在健康池内扫描。  
这对应钊审财的"方案3：基本面确认架构"。

---

## 四、文件修改清单汇总

| 阶段 | 文件 | 改动类型 | 改动内容 |
|------|------|---------|---------|
| P0 | `strategies/momentum.py` | 修改 | description 去"排名前10%" |
| P0 | `strategies/volume_breakout.py` | 修改 | 修复range(-10,-1)负数索引风险 |
| P0 | `strategies/limit_up_gene.py` | 修改 | _get_codes 增加全市场fallback |
| P0 | `strategies/base.py` | 修改 | win_rate 改为 Optional[float] = None |
| P0 | `strategies/[各策略].py` | 修改 | 不再调用 _calc_win_rate() |
| P0 | `core/engine.py` | 修改 | 移除 win_rate_weight |
| P1 | `strategies/base.py` | 新增方法 | _calc_stop_loss, _calc_take_profit |
| P1 | `strategies/[全部11个].py` | 修改 | extra中写入stop_loss_price/take_profit_price |
| P1 | `web/templates/index.html` | 修改 | 新增止损/止盈展示列 |
| P2 | `data/data_sources.py` | 新增类 | FundamentalDataSource |
| P2 | `data/fetcher.py` | 新增函数 | get_stock_fundamental |
| P2 | `data/local_cache.py` | 修改 | 增加基本面数据缓存 |
| P2 | `strategies/base.py` | 新增方法 | fundamental_filter |
| P2 | `strategies/base.py` | 修改 | screen() 中前置基本面过滤 |
| P2 | `strategies/limit_up_gene.py` | 覆盖 | fundamental_filter 更严格 |
| P2 | `strategies/momentum.py` | 覆盖 | fundamental_filter 更严格 |
| P2 | `strategies/volume_breakout.py` | 覆盖 | fundamental_filter 更严格 |
| P2 | `web/templates/index.html` | 修改 | 新增PE/ROE/增速列 + 估值标签 |
| P3 | `strategies/registry.py` | 修改 | 增加 direction, risk_level 字段 |
| P3 | `core/server.py` | 修改 | /api/strategies 返回 direction/risk_level |
| P3 | `web/templates/index.html` | 修改 | 策略卡片增加方向/风险标签 + 模块分组 |
| P3 | `strategies/registry.py` | 新增常量 | STRATEGY_MODULES |
| P4 | `portfolio/position_sizer.py` | 新增文件 | 仓位管理模块 |
| P4 | `core/engine.py` | 修改 | merge_results 增加 suggested_position |
| P4 | `data/data_sources.py` | 新增 | 板块强度数据源 |
| P4 | `core/engine.py` | 修改 | 基本面预筛股票池 |

---

## 五、验收标准

### 第一阶段验收
- [ ] `momentum.py` 描述中无"排名前10%"
- [ ] `volume_breakout.py` 在数据不足10根时不出异常
- [ ] `limit_up_gene.py` 涨停接口失败时fallback到全市场
- [ ] 全局搜索 `_calc_win_rate` 调用，确认策略层不再使用

### 第二阶段验收
- [ ] `/api/quick_screen` 返回的每只股票包含 `extra.stop_loss_price`
- [ ] `/api/result` 综合推荐中每只股票包含 `stop_loss_price`
- [ ] 前端结果表格可见止损/止盈列
- [ ] 持仓中当前价跌破止损价时标红

### 第三阶段验收
- [ ] 能成功获取全市场PE/ROE/增速数据
- [ ] PE>100 或 ROE<5% 的股票被策略过滤
- [ ] `limit_up_gene` 不再推荐基本面恶化的股票
- [ ] 前端可见PE/ROE列，PE>50标黄、>100标红

### 第四阶段验收
- [ ] `/api/strategies` 返回包含 direction 和 risk_level
- [ ] 前端策略卡片有"顺势"/"抄底"标签
- [ ] 可按模块一键选择策略组

---

## 六、风险提示

1. **基本面数据源稳定性**: akshare接口可能调整，需做好异常fallback（基本面过滤失败时允许通过，不打断策略执行）
2. **性能影响**: 基本面数据每日只需获取一次，缓存得当不会影响策略扫描速度
3. **止损价非交易指令**: 工程中的止损价仅为建议，不触发自动卖出
4. **回测数据偏差**: 引入基本面过滤后，历史回测结果会变，需要重新跑回测验证
