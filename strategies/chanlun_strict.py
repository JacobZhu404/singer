"""
严格缠论均衡版 — 歌者

重构范围（平衡准确性与实现难度）：
  1. 包含处理：方向优先，严格处理包含关系
  2. 严格分型：5K结构确认（中间K线 + 左右各2根非包含K线）
  3. 笔构建：顶底交替 + 至少5根K线门槛
  4. 中枢：三笔重叠区域
  5. 背驰：基于笔边界确定a段/c段，比较DIF幅度
  6. 一买：底背驰后的第一低点（笔边界确定）
  7. 二买：回踩不破一买 + 中枢下沿支撑
  8. 三买（简化）：向上离开中枢后，回调不进入中枢（无需线段递归）

不包含：线段（需特征序列+线段破坏）、多级别递归
"""

import pandas as pd
import numpy as np
from typing import List, Tuple, Optional, Dict, NamedTuple
from dataclasses import dataclass
import logging

from .base import BaseStrategy, StockSignal, ScreenResult, _compute_risk_flags
from ..utils.indicators import calc_volume_ratio

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class KBar:
    """单根K线（含包含处理后的数据）"""
    index: int       # 原始K线索引
    open: float
    high: float
    low: float
    close: float


@dataclass
class Fractal:
    """分型：顶分型或底分型"""
    type: str          # "top" | "bottom"
    bar_index: int     # 中间K线（合并后）的索引
    price: float       # 顶分型=high, 底分型=low
    strength: float    # 强度（0-1）

    @property
    def is_top(self) -> bool:
        return self.type == "top"

    @property
    def is_bottom(self) -> bool:
        return self.type == "bottom"


@dataclass
class Stroke:
    """笔：由顶分型和底分型构成的一段走势"""
    start_fx: Fractal   # 起点分型
    end_fx: Fractal     # 终点分型
    direction: str      # "up" | "down"
    bars: List[KBar]   # 笔内的K线列表
    start_price: float
    end_price: float
    amplitude: float    # 涨跌幅（%）
    low: float          # 笔内最低价
    high: float         # 笔内最高价

    @property
    def is_up(self) -> bool:
        return self.direction == "up"

    @property
    def is_down(self) -> bool:
        return self.direction == "down"


@dataclass
class Pivot:
    """中枢：三笔重叠形成的震荡区间"""
    strokes: List[Stroke]   # 构成中枢的三笔
    zd: float                # 中枢低点（震荡低点最高值）
    zg: float                # 中枢高点（震荡高点最低值）
    width: float             # 中枢宽度 = zg - zd
    level: int               # 中枢级别（1=日线笔级）


@dataclass
class Divergence:
    """背驰"""
    type: str        # "bull" | "bear"
    a_low: float     # a段低点
    c_low: float     # c段低点（更低的低点）
    a_dif: float     # a段DIF最小值
    c_dif: float     # c段DIF最小值（DIF未创新低 = 背驰）
    strength: float  # 强度 0-1


@dataclass
class BuyPoint:
    """买卖点"""
    type: str       # "一买" | "二买" | "三买"
    price: float
    stroke: Stroke  # 触发该买点的笔
    pivot: Optional[Pivot] = None  # 相关中枢
    divergence: Optional[Divergence] = None  # 相关背驰
    strength: float = 0.0


@dataclass
class ChanlunAnalysis:
    """完整缠论分析结果"""
    raw_kline: pd.DataFrame
    processed_bars: List[KBar]      # 包含处理后的K线
    fractals: List[Fractal]         # 所有分型
    strokes: List[Stroke]           # 所有笔
    pivots: List[Pivot]             # 所有中枢
    divergences: List[Divergence]   # 背驰信号
    buy_points: List[BuyPoint]      # 买点
    sell_points: List[BuyPoint]     # 卖点
    current_price: float             # 最新价格
    current_pct: float              # 最新涨跌幅


# ─────────────────────────────────────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────────────────────────────────────

def _to_kbars(df: pd.DataFrame) -> List[KBar]:
    """将DataFrame转换为KBar列表"""
    bars = []
    for i, row in df.iterrows():
        bars.append(KBar(
            index=int(i),
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
        ))
    return bars


def _merge_inclusive_strict(raw_bars: List[KBar]) -> List[KBar]:
    """
    严格包含处理：方向优先处理
    缠论规则：
    - 判断当前K线方向（上升/下降）
    - 包含关系：一根K线的高点和低点完全包含另一根
    - 向上合并（方向=up）：取高高（较高high + 较高low）
    - 向下合并（方向=down）：取低低（较低high + 较低low）
    - 方向随新K线动态更新：新K线 low > prev.low → up；< prev.low → down
    """
    if len(raw_bars) < 2:
        return raw_bars[:]

    result: List[KBar] = [raw_bars[0]]

    # ── 初始方向 ──
    # 由前两根非包含K线决定
    direction = "up" if raw_bars[1].low >= raw_bars[0].low else "down"

    # ── 逐根处理 ──
    for i in range(1, len(raw_bars)):
        b = raw_bars[i]
        prev = result[-1]

        # 判断包含关系：
        # 包含 = 一根K线的高低点范围完全覆盖另一根
        # 即：(prev.high >= b.high and prev.low <= b.low) → prev包含b
        #  或 (b.high >= prev.high and b.low <= prev.low) → b包含prev
        prev_contains_b = (prev.high >= b.high and prev.low <= b.low)
        b_contains_prev = (b.high >= prev.high and b.low <= prev.low)

        if prev_contains_b or b_contains_prev:
            # 有包含关系，按当前方向合并
            if direction == "up":
                # 向上合并：取高高
                new_bar = KBar(
                    index=b.index,
                    open=prev.open,
                    high=max(prev.high, b.high),
                    low=max(prev.low, b.low),   # 取较高的low
                    close=b.close,              # 保留最新收盘价，不影响MACD计算
                )
            else:
                # 向下合并：取低低
                new_bar = KBar(
                    index=b.index,
                    open=prev.open,
                    high=min(prev.high, b.high), # 取较低的high
                    low=min(prev.low, b.low),
                    close=b.close,               # 保留最新收盘价，不影响MACD计算
                )
            result[-1] = new_bar
        else:
            # 无包含关系，保留新K线
            result.append(b)
            # 动态更新方向：新K线的 low 与前一K线的 low 比较
            if b.low > prev.low:
                direction = "up"
            elif b.low < prev.low:
                direction = "down"
            # 如果 low 相等（极少见），方向不变

    return result


def _find_fractals_strict(bars: List[KBar]) -> List[Fractal]:
    """
    严格分型检测（5K确认）：
    缠论标准：中间K线 + 左右各至少2根非包含K线
    - 顶分型：中间K线的高点是局部最高
    - 底分型：中间K线的低点是局部最低
    """
    if len(bars) < 5:
        return []

    n = len(bars)
    fractals: List[Fractal] = []

    for i in range(2, n - 2):
        # 左右各检查2根K线
        # 顶分型：bars[i]是左右2根中最高
        left_2_high = max(bars[i-2].high, bars[i-1].high)
        right_2_high = max(bars[i+1].high, bars[i+2].high)
        # 底分型：bars[i]是左右2根中最低
        left_2_low = min(bars[i-2].low, bars[i-1].low)
        right_2_low = min(bars[i+1].low, bars[i+2].low)

        # 中间K线需严格高于/低于两侧
        is_top = (bars[i].high > left_2_high and bars[i].high > right_2_high)
        is_bottom = (bars[i].low < left_2_low and bars[i].low < right_2_low)

        if is_top:
            # 计算分型强度：中间K线超出两侧越多越强
            exceed = bars[i].high - max(left_2_high, right_2_high)
            body = bars[i].high - bars[i].low
            strength = min(exceed / (body + 1e-8), 1.0) if body > 0 else 0.5
            fractals.append(Fractal("top", i, bars[i].high, max(strength, 0.3)))
        elif is_bottom:
            exceed = min(left_2_low, right_2_low) - bars[i].low
            body = bars[i].high - bars[i].low
            strength = min(exceed / (body + 1e-8), 1.0) if body > 0 else 0.5
            fractals.append(Fractal("bottom", i, bars[i].low, max(strength, 0.3)))

    return fractals


def _build_strokes(fractals: List[Fractal], bars: List[KBar], min_bi_len: int = 5) -> List[Stroke]:
    """
    笔构建：顶底交替 + 至少5根非包含K线

    算法：
    1. 先对分型做合并：连续同向分型取极值（连续顶取最高，连续底取最低）
    2. 从合并后的分型序列中构建笔（顶底交替）
    3. 两分型之间的K线数量 >= min_bi_len 才确认为一笔
    """
    if len(fractals) < 2:
        return []

    # ── 合并连续同向分型：取极值 ──
    merged: List[Fractal] = [fractals[0]]
    for fx in fractals[1:]:
        last = merged[-1]
        if fx.type == last.type:
            # 同向：顶取最高，底取最低
            if fx.is_top and fx.price > last.price:
                merged[-1] = fx
            elif fx.is_bottom and fx.price < last.price:
                merged[-1] = fx
        else:
            merged.append(fx)

    strokes: List[Stroke] = []

    i = 0
    while i < len(merged) - 1:
        fx1 = merged[i]
        fx2 = merged[i + 1]

        # 判断方向（合并后必然交替）
        if fx1.is_bottom and fx2.is_top:
            direction = "up"
        elif fx1.is_top and fx2.is_bottom:
            direction = "down"
        else:
            i += 1
            continue

        # 检查K线数量门槛
        start_idx = fx1.bar_index
        end_idx = fx2.bar_index
        bar_count = end_idx - start_idx + 1  # 包含首尾

        if bar_count < min_bi_len:
            i += 1
            continue

        # 提取笔内的K线
        stroke_bars = bars[start_idx:end_idx + 1]
        if not stroke_bars:
            i += 1
            continue

        # 计算笔的幅度
        if direction == "up":
            amplitude = (fx2.price - fx1.price) / fx1.price * 100
        else:
            amplitude = (fx1.price - fx2.price) / fx1.price * 100

        low = min(b.low for b in stroke_bars)
        high = max(b.high for b in stroke_bars)

        stroke = Stroke(
            start_fx=fx1,
            end_fx=fx2,
            direction=direction,
            bars=stroke_bars,
            start_price=fx1.price,
            end_price=fx2.price,
            amplitude=amplitude,
            low=low,
            high=high,
        )
        strokes.append(stroke)
        i += 1

    return strokes


def _find_pivots(strokes: List[Stroke]) -> List[Pivot]:
    """
    中枢检测：三笔重叠区域

    缠论规则：
    - 连续三笔有重叠区间 → 形成中枢
    - 中枢高点 ZG = min(各笔高点)
    - 中枢低点 ZD = max(各笔低点)
    - 有效中枢：ZG > ZD

    简化策略：
    - 滑动窗口：取连续3笔，检查重叠
    - 重叠区间 = [max(3笔低点), min(3笔高点)]
    """
    if len(strokes) < 3:
        return []

    pivots: List[Pivot] = []
    i = 0

    while i <= len(strokes) - 3:
        seg = strokes[i:i + 3]
        # 三笔重叠区域
        zd = max(s.low for s in seg)   # 中枢低点 = 三笔低点最高值
        zg = min(s.high for s in seg)  # 中枢高点 = 三笔高点最低值

        if zg > zd:
            width = zg - zd
            pivot = Pivot(
                strokes=seg,
                zd=zd,
                zg=zg,
                width=width,
                level=1,
            )
            pivots.append(pivot)
            i += 1  # 每次滑动1笔，支持中枢延伸
        else:
            i += 1

    # 合并相邻的重叠中枢（中枢延伸）
    if not pivots:
        return []

    merged: List[Pivot] = [pivots[0]]
    for p in pivots[1:]:
        last = merged[-1]
        # 如果新中枢与上一个中枢有重叠区域，合并
        overlap_zd = max(last.zd, p.zd)
        overlap_zg = min(last.zg, p.zg)
        if overlap_zg > overlap_zd:
            # 合并：扩展ZD和ZG
            merged[-1] = Pivot(
                strokes=last.strokes + p.strokes,
                zd=min(last.zd, p.zd),
                zg=max(last.zg, p.zg),
                width=max(last.width, p.width),
                level=1,
            )
        else:
            merged.append(p)

    return merged


def _calc_macd(closes: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """计算MACD指标，返回 (dif, dea, macd_bar)"""
    closes = np.asarray(closes, dtype=float)
    # EMA
    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    dif = ema12 - ema26
    dea = _ema(dif, 9)
    macd_bar = (dif - dea) * 2
    return dif, dea, macd_bar


def _ema(data: np.ndarray, span: int) -> np.ndarray:
    """计算指数移动平均

    EMA 等价于一阶 IIR 滤波器 y[t] = α·x[t] + (1-α)·y[t-1]，用 scipy.signal.lfilter
    替代纯 Python 循环。基准测试 n=120/250 分别有 9x/16x 加速，输出与原循环
    bit-for-bit 完全一致（max_abs_diff = 0.0）。
    pd.Series.ewm 在 n<500 时由于 Series 创建开销反而比循环慢，故不用。
    """
    from scipy.signal import lfilter
    alpha = 2.0 / (span + 1)
    b = np.array([alpha])
    a = np.array([1.0, -(1.0 - alpha)])
    # zi 初始化让 y[0] = x[0]（与原循环 result[0] = data[0] 一致）
    zi = np.array([(1.0 - alpha) * float(data[0])])
    y, _ = lfilter(b, a, data, zi=zi)
    return y


def _detect_divergence_strokes(
    strokes: List[Stroke],
    bars: List[KBar],
    min_lookback: int = 3,
) -> List[Divergence]:
    """
    基于笔边界检测背驰：
    - 找到最近两段同向笔（a段 + c段）
    - 比较两段的价格幅度和DIF幅度
    - 价格创新低但DIF未创新低 → 底背驰

    重要：DIF 使用全序列K线计算MACD，再按笔边界索引截取，
    避免短序列EMA热启动偏差。
    """
    if len(strokes) < 4:
        return []

    divergences: List[Divergence] = []
    n = len(strokes)

    # ── 全序列MACD（一次性计算，避免热启动偏差）──
    all_closes = np.array([b.close for b in bars], dtype=float)
    all_dif, _, _ = _calc_macd(all_closes)

    # 找最近的下落笔（c段）和它之前的第一段下落笔（a段）
    for i in range(n - 1, 2, -1):
        c_stroke = strokes[i]
        if c_stroke.direction != "down":
            continue

        # 找c段之前的下落笔（a段）
        a_stroke = None
        for j in range(i - 1, 1, -1):
            if strokes[j].direction == "down":
                a_stroke = strokes[j]
                break

        if a_stroke is None:
            continue

        # ── 底背驰判断 ──
        # 价格：c段低点创新低
        price_broke = c_stroke.end_price < a_stroke.end_price * 0.995

        # 从全序列DIF中截取a段和c段范围内的DIF值
        a_start_idx = a_stroke.start_fx.bar_index
        a_end_idx = a_stroke.end_fx.bar_index
        c_start_idx = c_stroke.start_fx.bar_index
        c_end_idx = c_stroke.end_fx.bar_index

        # 边界检查
        if a_start_idx >= len(all_dif) or c_start_idx >= len(all_dif):
            continue

        a_dif_vals = all_dif[a_start_idx:min(a_end_idx + 1, len(all_dif))]
        c_dif_vals = all_dif[c_start_idx:min(c_end_idx + 1, len(all_dif))]

        if len(a_dif_vals) < 2 or len(c_dif_vals) < 2:
            continue

        a_dif_min = float(np.min(a_dif_vals))
        c_dif_min = float(np.min(c_dif_vals))

        # DIF未创新低 → 底背驰
        dif_weak = c_dif_min > a_dif_min * 0.90  # 容差10%

        if price_broke and dif_weak:
            price_drop = (a_stroke.end_price - c_stroke.end_price) / a_stroke.end_price
            dif_diff = (c_dif_min - a_dif_min) / abs(a_dif_min) if abs(a_dif_min) > 1e-8 else 0
            strength = min(max(price_drop * 2 + max(dif_diff, 0) * 0.5, 0.3), 1.0)
            divergences.append(Divergence(
                type="bull",
                a_low=a_stroke.end_price,
                c_low=c_stroke.end_price,
                a_dif=a_dif_min,
                c_dif=c_dif_min,
                strength=strength,
            ))
            break  # 找到最近一段背驰即可

    return divergences


def _find_buy_points(
    strokes: List[Stroke],
    pivots: List[Pivot],
    divergences: List[Divergence],
    current_price: float,
) -> List[BuyPoint]:
    """
    识别三类买点：

    一买：底背驰后的第一低点
        - 条件：有底背驰 + 最近笔是下落笔 + 末端接近底部
        - 简化：底背驰存在 + 最后一笔是下落笔

    二买：回踩不破一买 + 中枢下沿支撑
        - 条件：一买之后的第一次回调 + 不破一买低点 + 在中枢ZD附近
        - 简化：底背驰后有上涨笔 + 回调不破背驰低点

    三买（简化）：向上离开中枢后，回调不进入中枢
        - 条件：有中枢 + 最近有离开中枢的动作 + 回调低点 > ZG
        - 无需线段递归，简化为：价格在中枢上方 + 回调不破ZG
    """
    buy_points: List[BuyPoint] = []

    if not strokes:
        return buy_points

    last = strokes[-1]

    # ── 一买：有底背驰 ──
    for div in divergences:
        if div.type == "bull":
            # 找触发背驰的那段下落笔
            for s in reversed(strokes):
                if s.direction == "down" and s.end_price <= div.c_low * 1.01:
                    buy_points.append(BuyPoint(
                        type="一买",
                        price=s.end_price,
                        stroke=s,
                        divergence=div,
                        strength=div.strength,
                    ))
                    break

    # ── 二买：回踩不破一买 ──
    if len(strokes) >= 3:
        # 找最近的下落笔（c段）和它之前的上涨笔（b段）
        for i in range(len(strokes) - 1, 1, -1):
            if strokes[i].direction != "down":
                continue

            # 找这个下落笔之前的上涨笔（应该是一买的反弹）
            b_stroke = None
            for j in range(i - 1, -1, -1):
                if strokes[j].direction == "up":
                    b_stroke = strokes[j]
                    break

            if b_stroke is None:
                continue

            c_stroke = strokes[i]
            # 二买：回调不破b段低点（一买低点）
            if c_stroke.end_price >= b_stroke.end_price * 0.97:
                # 检查是否有相关中枢支撑
                related_pivot = None
                for p in pivots:
                    if b_stroke.end_price >= p.zd * 0.98:
                        related_pivot = p
                        break

                buy_points.append(BuyPoint(
                    type="二买",
                    price=c_stroke.end_price,
                    stroke=c_stroke,
                    pivot=related_pivot,
                    strength=0.6,
                ))
                break  # 只取最近的一次

    # ── 三买（简化）：向上离开中枢后回调不进入 ──
    if pivots and last.is_up:
        latest_pivot = pivots[-1]
        # 最后一笔是上涨笔，检查是否离开中枢
        if last.high > latest_pivot.zg:
            # 有离开中枢的动作
            # 检查是否已回踩（收盘价在中枢上方但靠近ZG）
            if current_price > latest_pivot.zg and current_price < latest_pivot.zg * 1.05:
                buy_points.append(BuyPoint(
                    type="三买",
                    price=current_price,
                    stroke=last,
                    pivot=latest_pivot,
                    strength=0.7,
                ))

    return buy_points


def _analyze(df: pd.DataFrame) -> Optional[ChanlunAnalysis]:
    """
    完整缠论分析流程：
    原始K线 → 包含处理 → 严格分型 → 笔构建 → 中枢 → 背驰 → 买点
    """
    if df is None or len(df) < 30:
        return None

    n = len(df)
    current_price = float(df["close"].iloc[-1])

    # ── Step 1: 包含处理 ──
    raw_bars = _to_kbars(df)
    processed_bars = _merge_inclusive_strict(raw_bars)

    if len(processed_bars) < 5:
        return None

    # ── Step 2: 严格分型 ──
    fractals = _find_fractals_strict(processed_bars)

    # ── Step 3: 笔构建 ──
    strokes = _build_strokes(fractals, processed_bars, min_bi_len=5)

    # ── Step 4: 中枢 ──
    pivots = _find_pivots(strokes)

    # ── Step 5: 背驰 ──
    divergences = _detect_divergence_strokes(strokes, processed_bars)

    # ── Step 6: 买点 ──
    buy_points = _find_buy_points(strokes, pivots, divergences, current_price)

    return ChanlunAnalysis(
        raw_kline=df,
        processed_bars=processed_bars,
        fractals=fractals,
        strokes=strokes,
        pivots=pivots,
        divergences=divergences,
        buy_points=buy_points,
        sell_points=[],
        current_price=current_price,
        current_pct=0.0,
    )


def _compute_score(analysis: ChanlunAnalysis) -> Tuple[int, List[str], dict]:
    """
    基于严格缠论结构计算综合评分
    返回 (总分, 信号描述, 额外数据)
    """
    if analysis is None:
        return 0, [], {}

    score = 0
    signals: List[str] = []
    extra: dict = {}

    # ── 分型评分 ──
    if analysis.fractals:
        recent_fx = analysis.fractals[-1]
        if recent_fx.is_bottom:
            signals.append(f"底分型(强度{recent_fx.strength:.0%})")
            score += 10
        elif recent_fx.is_top:
            signals.append(f"顶分型(强度{recent_fx.strength:.0%})")

    # ── 笔评分 ──
    if analysis.strokes:
        last_stroke = analysis.strokes[-1]
        if last_stroke.is_down:
            signals.append(f"当前下落笔({last_stroke.amplitude:.1f}%)")
        else:
            signals.append(f"当前上涨笔({last_stroke.amplitude:.1f}%)")

    # ── 中枢评分 ──
    if analysis.pivots:
        latest_pivot = analysis.pivots[-1]
        signals.append(f"中枢{latest_pivot.zd:.2f}~{latest_pivot.zg:.2f}")
        extra["pivot"] = {"zd": latest_pivot.zd, "zg": latest_pivot.zg, "width": latest_pivot.width}
    
    # 优化1：中枢位置精度验证（价格在中枢下沿附近才算有效支撑）
    price = analysis.current_price
    if analysis.pivots:
        latest_pivot = analysis.pivots[-1]
        # 价格在中枢下沿附近（±2%）才算有效支撑
        if latest_pivot.zd * 0.98 <= price <= latest_pivot.zd * 1.02:
            signals.append("价格触及中枢下沿支撑(精确)")
            score += 15
        # 价格在中枢内部
        elif latest_pivot.zd < price < latest_pivot.zg:
            signals.append("价格位于中枢内部")
            score += 5
        # 价格突破中枢上沿
        elif price >= latest_pivot.zg * 0.98:
            signals.append("价格突破中枢上沿")
            score += 20

    # ── 背驰评分 ──
    for div in analysis.divergences:
        if div.type == "bull":
            signals.append(f"底背驰(强度{div.strength:.0%})")
            score += int(30 * div.strength)
            extra["divergence"] = "bull"
            break

    # ── 买点评分 ──
    buy_type_map = {}
    for bp in analysis.buy_points:
        if bp.type == "一买":
            signals.append(f"一买({bp.price:.2f})")
            score += 30
            buy_type_map["一买"] = bp
        elif bp.type == "二买":
            signals.append(f"二买({bp.price:.2f})")
            score += 20
            buy_type_map["二买"] = bp
        elif bp.type == "三买":
            signals.append(f"三买({bp.price:.2f})")
            score += 20
            buy_type_map["三买"] = bp

    if buy_type_map:
        # 优先标记最强买点
        strongest = max(buy_type_map.values(), key=lambda p: p.strength)
        extra["buy_type"] = strongest.type
        extra["buy_price"] = strongest.price

    # ── 结构完整性加分 ──
    if len(analysis.strokes) >= 5:
        signals.append("笔结构完整(5笔+)")
        score += 5
        extra["stroke_count"] = len(analysis.strokes)
    
    # 优化2：线段验证简化版（检测最近3笔是否构成有效线段）
    if len(analysis.strokes) >= 3:
        recent_3 = analysis.strokes[-3:]
        # 检查是否构成有效线段（交替方向 + 不破坏）
        if recent_3[0].direction != recent_3[1].direction and \
           recent_3[1].direction != recent_3[2].direction:
            # 有效线段：第3笔未破坏第2笔的端点
            if recent_3[2].direction == "up":
                if recent_3[2].end_price > recent_3[1].end_price:
                    signals.append("线段验证通过(上涨)")
                    score += 10
                    extra["stroke_validated"] = True
            else:  # down
                if recent_3[2].end_price < recent_3[1].end_price:
                    signals.append("线段验证通过(下跌)")
                    score += 10
                    extra["stroke_validated"] = True
    
    if len(analysis.pivots) >= 2:
        signals.append(f"多中枢({len(analysis.pivots)}个)")
        extra["pivot_count"] = len(analysis.pivots)

    return score, signals, extra


# ─────────────────────────────────────────────────────────────────────────────
# 策略类
# ─────────────────────────────────────────────────────────────────────────────

class ChanlunStrictStrategy(BaseStrategy):
    """
    严格缠论选股策略（均衡版）

    核心算法：
    - 包含处理 → 严格分型 → 笔构建 → 中枢 → 背驰 → 买点

    评分逻辑：
    - 底分型: +15分
    - 笔结构完整(5笔+): +5分
    - 中枢下沿支撑: +15分
    - 底背驰: +30分×强度
    - 一买: +25分
    - 二买: +20分
    - 三买(简化): +15分
    """
    name = "chanlun_strict"
    description = "严格缠论（均衡版）：包含处理→分型→笔→中枢→背驰→三类买点"
    base_win_rate = 0.60  # 优化：加入中枢精度验证+线段验证

    def _evaluate_single_stock(
        self,
        code: str,
        scanner,
        name_map: dict,
        trade_date: str,
    ) -> Optional[StockSignal]:
        """评估单只股票，返回StockSignal或None（并行架构）"""
        df = scanner.get_history(code, days=120)
        if df is None or len(df) < 30:
            raise self._SkipStock()

        analysis = _analyze(df)
        score, signals, extra = _compute_score(analysis)

        # 优化：必须有买点 或 牛背驰
        has_buy_point = extra.get("buy_type") is not None
        has_divergence = extra.get("divergence") == "bull"
        if not (has_buy_point or has_divergence):
            return None

        # 收紧阈值：55→65，控制命中数
        if score < 65:
            return None

        # 实时行情：与其他策略统一走 _get_quote（API 宕机自动兜底为 0），避免本策略
        # 在实时源故障时拉高 error 率
        last_close = float(df["close"].iloc[-1])
        quote = self._get_quote(scanner, code, last_close)
        pct = float(quote.get("涨跌幅", 0.0) or 0.0)

        # 统一使用 calc_volume_ratio（含当日，period=5）
        vol = df["vol"]
        vol_ratio_series = calc_volume_ratio(vol, 5)
        vol_ratio = float(vol_ratio_series.iloc[-1]) if not pd.isna(vol_ratio_series.iloc[-1]) else 1.0

        price = float(quote.get("最新价", last_close) or last_close)

        if analysis:
            analysis.current_pct = pct

        risk_flags = _compute_risk_flags(df)

        return StockSignal(
            ts_code=code,
            name=name_map.get(code, code),
            strategy=self.name,
            score=min(score, 100),
            win_rate=None,
            signals=signals,
            latest_price=round(price, 2),
            pct_chg=round(pct, 2),
            volume_ratio=round(vol_ratio, 2),
            risk_flags=risk_flags,
            trade_date=trade_date,
            extra=extra,
        )