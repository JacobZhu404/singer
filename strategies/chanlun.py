"""
缠论选股策略（启发式近似版）

⚠️ 注意：本策略是缠论的简化近似实现，非标准缠论。与 chanlun_strict 的关键差异：

  1. 无"笔"概念 — 直接从分型跳到中枢，跳过了缠论推导链的核心环节
  2. 中枢检测非标准 — 取中间1/3段K线的高低点范围，
     而非缠论定义的"三段连续走势类型重叠区间"
  3. 背驰检测简化 — MACD柱状体前后3根比较，非基于笔边界的a/c段DIF对比

推荐使用 chanlun_strict（严格缠论均衡版），它完成了：
  包含处理 → 严格分型(5K) → 笔 → 中枢(三笔重叠) → 背驰(笔边界DIF) → 三类买点

本策略仅作为快速扫描备选保留，不作为缠论信号的主要依据。
"""

import pandas as pd
import numpy as np
from typing import List, Tuple, Optional
import logging

from .base import BaseStrategy, StockSignal, ScreenResult, _compute_risk_flags
from ..utils.indicators import calc_macd
from ..data.fetcher import market_scanner, get_latest_trade_date

logger = logging.getLogger(__name__)

# ── 内部工具函数 ──────────────────────────────────────────────────────────────

def _merge_inclusive_bars(kline: pd.DataFrame) -> pd.DataFrame:
    """
    缠论包含处理：将相邻的包含关系K线合并

    缠论规则：
    - 上升K线（第一根 low >= 第二根 low）：取高高（取两根中较高的high和较高的low）
    - 下降K线（第一根 high <= 第二根 high）：取低低（取两根中较低的low和较低的high）

    包含处理后，所有剩余K线的高点和低点不再互相包含，可以直接判断分型。
    """
    df = kline[["open", "high", "low", "close"]].copy()
    merged = []
    i = 0
    n = len(df)

    while i < n:
        if len(merged) == 0:
            merged.append(df.iloc[i])
            i += 1
            continue

        prev = merged[-1]
        curr = df.iloc[i]

        prev_high, prev_low = float(prev["high"]), float(prev["low"])
        curr_high, curr_low = float(curr["high"]), float(curr["low"])

        # 判断包含关系
        # 上升K线：prev_low >= curr_low 且 prev_high <= curr_high → 包含向上
        # 下降K线：prev_high <= curr_high 且 prev_low >= curr_low → 包含向下
        if prev_low >= curr_low and prev_high <= curr_high:
            # 包含向上：合并为高高
            merged.pop()
            merged.append(pd.Series({
                "open": prev_low,
                "high": max(prev_high, curr_high),
                "low": prev_low,
                "close": max(prev_high, curr_high),
            }))
            i += 1
        elif prev_high <= curr_high and prev_low >= curr_low:
            # 包含向下：合并为低低
            merged.pop()
            merged.append(pd.Series({
                "open": prev_low,
                "high": min(prev_high, curr_high),
                "low": prev_low,
                "close": min(prev_high, curr_high),
            }))
            i += 1
        else:
            # 无包含关系，保留
            merged.append(curr)
            i += 1

    if not merged:
        return kline
    return pd.DataFrame(merged, columns=["open", "high", "low", "close"]).reset_index(drop=True)


def _find_fractals(kline: pd.DataFrame) -> Tuple[list, list]:
    """
    遍历找出所有分型，返回 (底分型索引列表, 顶分型索引列表)

    严格缠论流程：
    1. 先做包含处理（合并包含关系的K线）
    2. 在合并后的序列上识别分型
    """
    # 包含处理
    merged = _merge_inclusive_bars(kline)
    if len(merged) < 3:
        return [], []

    highs = merged["high"].values
    lows = merged["low"].values
    n = len(merged)
    bottom_idxs = []
    top_idxs = []

    for i in range(1, n - 1):
        # 底分型：中间K线最低（严格小于等于两侧）
        if lows[i] <= lows[i-1] and lows[i] <= lows[i+1]:
            bottom_idxs.append(i)
        # 顶分型：中间K线最高（严格大于等于两侧）
        elif highs[i] >= highs[i-1] and highs[i] >= highs[i+1]:
            top_idxs.append(i)

    return bottom_idxs, top_idxs


def _detect_divergence_macd(kline: pd.DataFrame, lookback: int = 20) -> Tuple[Optional[str], float]:
    """
    检测MACD背驰（改进版：笔边界比较）

    缠论背驰定义：
    - 底背离：a段下跌 + c段下跌创新低，但 c段的DIF幅度 < a段
    - 顶背离：a段上涨 + c段上涨创新高，但 c段的DIF幅度 < a段

    实现策略：
    1. 在lookback窗口内，分出最近两段下跌
    2. 比较两段价格幅度和对应 DIF 幅度
    3. 价格新低但 DIF 未跟随 → 底背离

    返回 (类型, 背离强度0-1)
    """
    close = kline["close"]
    n = len(close)

    if n < lookback + 10:
        return None, 0.0

    # 计算 MACD
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    macd_bar = (dif - dea) * 2

    # 分析窗口：最近 lookback 根
    start = max(0, n - lookback)
    win_close = close.iloc[start:]
    win_dif = dif.iloc[start:]
    win_bar = macd_bar.iloc[start:]

    if len(win_close) < 5:
        return None, 0.0

    # ── 找最近两段下跌的区间 ──
    seg_len = min(10, len(win_close) // 2)
    # c 段终点：最近 seg_len 根的最低点
    recent_low_val = win_close.iloc[-seg_len:].min()
    recent_low_idx_local = win_close.iloc[-seg_len:].idxmin()
    recent_low_pos = win_close.index.get_loc(recent_low_idx_local) - start

    # a 段：c段之前同样长度
    prev_seg_start = max(0, recent_low_pos - seg_len)
    prev_seg_close = win_close.iloc[prev_seg_start:recent_low_pos]

    if len(prev_seg_close) < 3:
        return None, 0.0

    prev_low_val = prev_seg_close.min()

    # ── 底背离判断 ──
    # c段价格创了新低（低于a段低点），但 DIF 未创新低
    price_broke = recent_low_val < prev_low_val * 0.995   # 价格新低（容差5‰）
    # c段对应 DIF
    c_dif_vals = win_dif.iloc[
        max(0, recent_low_pos - seg_len):recent_low_pos
    ]
    c_dif_min = float(c_dif_vals.min()) if len(c_dif_vals) > 0 else 0.0
    # a段对应 DIF
    a_dif_vals = win_dif.iloc[prev_seg_start:recent_low_pos]
    a_dif_min = float(a_dif_vals.min()) if len(a_dif_vals) > 0 else 0.0
    dif_weak = c_dif_min > a_dif_min * 0.95   # DIF 未跟随创新低

    if price_broke and dif_weak:
        price_drop_ratio = max(0.0, (prev_low_val - recent_low_val) / prev_low_val)
        dif_diff_ratio = max(0.0, (c_dif_min - a_dif_min) / abs(a_dif_min)) if abs(a_dif_min) > 1e-8 else 0
        strength = min(max(price_drop_ratio + dif_diff_ratio * 0.5, 0.3), 1.0)
        return "底背离", strength

    # ── 辅助：MACD柱状体收缩（辅助触发）──
    bar_sum_recent = win_bar.iloc[-3:].sum()
    bar_sum_prev = win_bar.iloc[-6:-3].sum() if len(win_bar) >= 6 else bar_sum_recent
    if bar_sum_prev < 0 and bar_sum_recent > bar_sum_prev:
        strength = min(abs(bar_sum_recent - bar_sum_prev) / abs(bar_sum_prev) if bar_sum_prev != 0 else 0, 1.0)
        return "底背离", max(strength, 0.3)

    return None, 0.0


def _detect_zhongshu(kline: pd.DataFrame, lookback: int = 30) -> Tuple[Optional[float], Optional[float]]:
    """
    简化中枢检测：在lookback窗口内，
    取中间段的重叠区域作为中枢（高点和低点）
    返回 (中枢低点, 中枢高点) 或 (None, None)
    """
    n = len(kline)
    if n < 20:
        return None, None

    start = max(0, n - lookback)
    sub = kline.iloc[start:]

    # 取中间1/3段的高点最低值和低点最高值（核心震荡区间）
    third = max(1, len(sub) // 3)
    mid = sub.iloc[third: 2*third]

    if len(mid) < 3:
        return None, None

    zg = mid["high"].max()    # 中枢高点
    zd = mid["low"].min()     # 中枢低点

    # 有效中枢：高点 > 低点
    if zg > zd and (zg - zd) / zd < 0.2:  # 震荡幅度不超过20%
        return zd, zg
    return None, None


def _is_near_bollinger_lower(kline: pd.DataFrame, threshold: float = 0.05) -> bool:
    """价格是否接近布林下轨（5%以内）"""
    close = kline["close"]
    if len(close) < 20:
        return False
    mid = close.rolling(20).mean()
    std = close.rolling(20).std()
    lower = mid - 2 * std
    latest_close = close.iloc[-1]
    latest_lower = lower.iloc[-1]
    if pd.isna(latest_lower) or latest_lower <= 0:
        return False
    return (latest_close - latest_lower) / latest_lower <= threshold


def _volume_shrink(kline: pd.DataFrame, window: int = 5) -> bool:
    """最近window根K线是否缩量（量能递减）"""
    vol = kline["vol"]
    if len(vol) < window + 1:
        return False
    recent = vol.iloc[-window:].values
    prev = vol.iloc[-window-1:-1].values
    return np.mean(recent) < np.mean(prev) * 0.8


def _chanlun_score(kline: pd.DataFrame) -> Tuple[int, List[str], dict]:
    """
    缠论综合评分
    返回 (总分, 信号描述列表, 额外数据)
    """
    n = len(kline)
    if n < 30:
        return 0, [], {}

    close = kline["close"]
    signals = []
    score = 0
    extra = {}

    # ── 1. 分型检测 ──
    bottom_idxs, top_idxs = _find_fractals(kline)
    has_bottom = len(bottom_idxs) > 0 and bottom_idxs[-1] >= n - 3
    has_top = len(top_idxs) > 0 and top_idxs[-1] >= n - 3

    if has_bottom:
        signals.append("底分型")
        score += 15
    if has_top:
        signals.append("顶分型")

    # ── 2. MACD背驰 ──
    div_type, div_strength = _detect_divergence_macd(kline, lookback=25)
    if div_type == "底背离":
        signals.append(f"MACD底背离({div_strength:.0%})")
        score += int(25 * max(div_strength, 0.3))
        extra["divergence"] = div_type

    # ── 3. 中枢检测 ──
    zd, zg = _detect_zhongshu(kline, lookback=30)
    if zd is not None:
        signals.append(f"中枢{zd:.2f}~{zg:.2f}")
        extra["zhongshu"] = {"zd": zd, "zg": zg}
        # 价格在中枢下方（接近买点区域）
        latest_close = close.iloc[-1]
        if zd * 1.02 >= latest_close:  # 价格接近中枢下沿
            signals.append("价格触及中枢下沿支撑")
            score += 15
        elif latest_close < zg * 1.01:
            signals.append("价格在中枢内偏下")
            score += 8

    # ── 4. 布林下轨企稳 ──
    if _is_near_bollinger_lower(kline, threshold=0.05):
        signals.append("布林下轨附近")
        score += 15

    # ── 5. 缩量整理后（动能积累） ──
    if _volume_shrink(kline, window=5):
        signals.append("缩量整理")
        score += 10

    # ── 6. 量价配合（放量突破布林中轨） ──
    if n >= 20:
        vol = kline["vol"]
        ma_vol = vol.rolling(20).mean()
        mid = close.rolling(20).mean()
        latest = close.iloc[-1]
        if not (pd.isna(mid.iloc[-1]) or pd.isna(ma_vol.iloc[-1]) or pd.isna(vol.iloc[-1])):
            vol_ratio = vol.iloc[-1] / ma_vol.iloc[-1]
            if vol_ratio > 1.5 and latest > mid.iloc[-1]:
                signals.append(f"放量({vol_ratio:.1f}x)突破中轨")
                score += 10

    # ── 7. 一买特征：底背离 + 布林下轨 + 缩量 ──
    if div_type == "底背离" and _is_near_bollinger_lower(kline, 0.05):
        signals.append("一买特征（底背驰+下轨）")
        score += 20
        extra["buy_type"] = "一买"

    # ── 8. 二买特征：回调不破新低 + 中枢支撑 ──
    if zd is not None and has_bottom:
        latest_close = close.iloc[-1]
        last_bottom_price = close.iloc[bottom_idxs[-1]] if bottom_idxs else latest_close
        if latest_close > last_bottom_price * 0.98 and latest_close < zg:
            signals.append("二买特征（回踩不破）")
            score += 15
            extra["buy_type"] = "二买"

    # ── 9. 三买特征：向上离开中枢后回调不进入中枢 ──
    # 严格缠论三买：价格向上离开中枢 → 回踩 → 回踩低点不跌回中枢上方
    if zd is not None and zg is not None:
        latest_close = close.iloc[-1]
        if latest_close > zg:
            # 至少有离开中枢的动作，检查是否在回调中（价格在中枢上方但靠近ZG）
            if latest_close < zg * 1.03:   # 回调后仍在中枢上沿附近
                signals.append("三买特征（回踩不破中枢）")
                score += 20
                extra["buy_type"] = "三买"
            else:
                # 刚刚突破，还未回踩
                signals.append("三买特征（突破后整理）")
                score += 10
                extra["buy_type"] = "三买"

    return score, signals, extra


def _detect_top_divergence_macd(kline: pd.DataFrame, lookback: int = 20) -> Tuple[Optional[str], float]:
    """
    检测MACD顶背离
    - 顶背离: 价格创出新高，但MACD快线(DIF)没有创新高
    返回 (类型, 背离强度0-1)
    """
    close = kline["close"]
    n = len(close)

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    macd_bar = (dif - dea) * 2

    start = max(0, n - lookback)
    recent_close = close.iloc[start:]
    recent_dif = dif.iloc[start:]
    recent_bar = macd_bar.iloc[start:]

    if len(recent_close) < 5:
        return None, 0.0

    # 顶背离：最近价格上涨，但MACD柱状体收缩（上涨动能减弱）
    bar_sum_recent = recent_bar.iloc[-3:].sum()
    bar_sum_prev = recent_bar.iloc[-6:-3].sum() if len(recent_bar) >= 6 else bar_sum_recent

    if bar_sum_prev > 0 and bar_sum_recent < bar_sum_prev:
        strength = min(abs(bar_sum_recent - bar_sum_prev) / abs(bar_sum_prev) if bar_sum_prev != 0 else 0, 1.0)
        return "顶背离", strength

    return None, 0.0


def _is_break_below_zhongshu(kline: pd.DataFrame, lookback: int = 30) -> Tuple[bool, Optional[float]]:
    """
    检测价格是否跌破中枢下沿
    返回: (是否跌破, 中枢下沿价格)
    """
    n = len(kline)
    if n < lookback:
        return False, None

    zd, zg = _detect_zhongshu(kline, lookback=lookback)
    if zd is None:
        return False, None

    close = kline["close"]
    latest_close = close.iloc[-1]
    prev_close = close.iloc[-2] if n >= 2 else latest_close

    # 昨日在中枢下沿之上，今日跌破
    if prev_close >= zd * 0.98 and latest_close < zd:
        return True, zd

    return False, zd


def _detect_chanlun_sell_signals(kline: pd.DataFrame) -> Tuple[bool, List[str], float]:
    """
    缠论卖点综合检测
    返回: (是否触发卖点, 卖点描述列表, 卖点强度0-1)
    """
    n = len(kline)
    if n < 30:
        return False, [], 0.0

    signals = []
    strength = 0.0
    close = kline["close"]
    high = kline["high"]
    latest_close = close.iloc[-1]

    # ── 中枢统一检测（避免重复调用）──
    zd, zg = _detect_zhongshu(kline, lookback=30)

    # ── 1. 顶分型 ──
    bottom_idxs, top_idxs = _find_fractals(kline)
    has_top = len(top_idxs) > 0 and top_idxs[-1] >= n - 3
    if has_top:
        signals.append("顶分型")
        strength += 0.25

    # ── 2. MACD顶背离 ──
    div_type, div_strength = _detect_top_divergence_macd(kline, lookback=25)
    if div_type == "顶背离":
        signals.append(f"MACD顶背离({div_strength:.0%})")
        strength += 0.30 * max(div_strength, 0.3)

    # ── 3. 跌破中枢下沿 ──
    if zd is not None:
        prev_close = close.iloc[-2] if n >= 2 else latest_close
        broke_below = prev_close >= zd * 0.98 and latest_close < zd
        if broke_below:
            signals.append(f"跌破中枢下沿({zd:.2f})")
            strength += 0.30

    # ── 4. 价格远离中枢上沿且放量滞涨 ──
    if zg is not None and latest_close > zg * 1.05:
        # 价格已远离中枢上沿5%以上，检查是否滞涨
        vol = kline["vol"]
        if n >= 6:
            avg_vol_5 = vol.iloc[-6:-1].mean()
            today_vol = vol.iloc[-1]
            pct_chg = close.pct_change().iloc[-1] * 100 if n >= 2 else 0
            if today_vol > avg_vol_5 * 1.3 and pct_chg < 1.0:
                signals.append("放量滞涨（远离中枢）")
                strength += 0.20

    # ── 5. 一卖特征：顶背离 + 远离中枢上沿 ──
    if div_type == "顶背离" and zg is not None and latest_close > zg * 1.03:
        signals.append("一卖特征（顶背驰+远离中枢）")
        strength += 0.25

    # ── 6. 二卖特征：反弹不创新高 + 顶分型 ──
    if has_top and len(top_idxs) >= 2:
        prev_top_price = high.iloc[top_idxs[-2]]
        curr_top_price = high.iloc[top_idxs[-1]]
        if curr_top_price < prev_top_price * 1.01:
            signals.append("二卖特征（反弹不创新高）")
            strength += 0.20

    # ── 7. 三卖特征：向下离开中枢后反弹不进入中枢 ──
    if zd is not None and latest_close < zd:
        # 已在中枢下方，检查昨日是否尝试反弹但未进入中枢
        if n >= 2:
            prev_close_val = close.iloc[-2]
            if prev_close_val < zd and latest_close < zd:
                signals.append("三卖特征（离开中枢后不返回）")
                strength += 0.20

    triggered = strength >= 0.35
    return triggered, signals, min(strength, 1.0)


# ── 策略类 ───────────────────────────────────────────────────────────────────

class ChanlunStrategy(BaseStrategy):
    """
    缠论选股策略
    基于缠中说禅理论，识别分型、笔、中枢、背驰及三类买点信号
    """
    name = "chanlun"
    description = "缠论(启发式近似版) - 无笔构建，中枢非标准，推荐用chanlun_strict"
    base_win_rate = 0.58

    def screen(self, stock_list: pd.DataFrame, scanner=None) -> ScreenResult:
        if scanner is None:
            scanner = market_scanner
        scanner.load()

        name_map = self._get_name_map(stock_list)
        trade_date = get_latest_trade_date()

        code_col = "代码" if "代码" in stock_list.columns else "ts_code"
        if stock_list.empty or code_col not in stock_list.columns:
            codes = []
        else:
            codes = stock_list[code_col].astype(str).tolist()

        candidates: List[StockSignal] = []
        scanned = 0

        for code in codes:
            try:
                kline = scanner.get_history(code, days=120)
                if kline is None or len(kline) < 30:
                    continue

                scanned += 1
                score, signals, extra = _chanlun_score(kline)

                if score < 40:
                    continue

                # 实时行情
                quote = scanner.get_realtime(code)
                pct = quote.get("涨跌幅", 0.0) or 0.0
                # 量比改为基于K线成交量计算（5日均量为基准）
                vol = kline["vol"]
                vol_ma5 = vol.rolling(5).mean().iloc[-1]
                vol_ratio_val = float(vol.iloc[-1] / vol_ma5) if (not pd.isna(vol_ma5) and vol_ma5 > 0) else 1.0
                price = quote.get("最新价", quote.get("close", kline["close"].iloc[-1])) or kline["close"].iloc[-1]

                win_rate = self._calc_win_rate(score, signals)
                risk_flags = _compute_risk_flags(kline)

                candidates.append(StockSignal(
                    ts_code=code,
                    name=name_map.get(code, code),
                    strategy=self.name,
                    score=min(score, 100),
                    win_rate=win_rate,
                    signals=signals,
                    latest_price=float(price),
                    pct_chg=float(pct),
                    volume_ratio=vol_ratio_val,
                    risk_flags=risk_flags,
                    trade_date=trade_date,
                    extra=extra,
                ))

            except Exception as e:
                logger.debug(f"[缠论策略] {code} 计算失败: {e}")

        candidates.sort(key=lambda x: x.score, reverse=True)
        top = candidates[:self.top_n]
        return ScreenResult(
            strategy_name=self.name,
            strategy_desc=self.description,
            signals=top,
            trade_date=trade_date,
            total_scanned=scanned,
        )
