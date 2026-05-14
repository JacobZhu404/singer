"""
技术指标计算工具库
所有指标基于 pandas DataFrame 计算，不依赖 talib
"""

import pandas as pd
import numpy as np
from typing import Tuple


def ema(series: pd.Series, period: int) -> pd.Series:
    """指数移动平均"""
    return series.ewm(span=period, adjust=False).mean()


def sma(series: pd.Series, period: int) -> pd.Series:
    """简单移动平均"""
    return series.rolling(window=period).mean()


def calc_macd(
    close: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """
    计算 MACD 指标
    返回: (DIF, DEA, MACD柱)
    """
    ema_fast = ema(close, fast)
    ema_slow = ema(close, slow)
    dif = ema_fast - ema_slow
    dea = ema(dif, signal)
    macd_bar = (dif - dea) * 2
    return dif, dea, macd_bar


def calc_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """计算RSI"""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, adjust=False).mean()
    avg_loss = loss.ewm(com=period - 1, adjust=False).mean()
    rs = avg_gain / (avg_loss + 1e-8)
    rsi = 100 - 100 / (1 + rs)
    return rsi


def calc_bollinger(
    close: pd.Series,
    period: int = 20,
    std_dev: float = 2.0
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """计算布林带"""
    mid = sma(close, period)
    std = close.rolling(period).std()
    upper = mid + std_dev * std
    lower = mid - std_dev * std
    return upper, mid, lower


def calc_ma(close: pd.Series, periods: list = [5, 10, 20, 60]) -> dict:
    """计算多周期均线"""
    return {f"ma{p}": sma(close, p) for p in periods}


def calc_volume_ratio(vol: pd.Series, period: int = 5) -> pd.Series:
    """量比：当日量 / 过去N日平均量（含当日，统一标准）"""
    avg_vol = vol.rolling(period).mean()
    return vol / (avg_vol + 1e-8)


def detect_gap_up(
    high: pd.Series,
    low: pd.Series,
    open_: pd.Series,
    close: pd.Series
) -> pd.Series:
    """
    跳空缺口检测：今日最低价 > 昨日最高价
    返回 bool Series
    """
    prev_high = high.shift(1)
    return low > prev_high


def is_red_candle(open_: pd.Series, close: pd.Series) -> pd.Series:
    """是否为阳线（收盘 > 开盘）"""
    return close > open_


def td_sequential_count(close: pd.Series, high: pd.Series = None, low: pd.Series = None) -> pd.Series:
    """
    神奇九转 TD Sequential 计数（严格版）

    买入序列：连续 N 天收盘价 < 4天前收盘价，N ∈ [1,9]
    卖出序列：连续 N 天收盘价 > 4天前收盘价，N ∈ [1,9]
    买入/卖出序列互不干扰，序列之间需间隔 4 根以上 bar

    完美Bar（Perfection Bar）校验：
      - 买入计数 9 成立条件：第9根 bar 收盘 < 第8根 bar 最低
      - 卖出计数 9 成立条件：第9根 bar 收盘 > 第8根 bar 最高
      - 不满足完美Bar时，count 回到 8（继续等待）

    Args:
        close: 收盘价序列
        high: 最高价序列（用于卖出序列完美Bar校验）
        low:  最低价序列（用于买入序列完美Bar校验）
              若不传入 low，完美Bar校验跳过（兼容旧调用）
    Returns:
        Series: 正数=买入计数, 负数=卖出计数, 0=无活跃序列
    """
    diff = close - close.shift(4)
    count = pd.Series(0, index=close.index, dtype=int)
    # 记录当前序列方向: 1=买入, -1=卖出, 0=无活跃序列
    direction = pd.Series(0, index=close.index, dtype=int)

    for i in range(4, len(close)):
        if pd.isna(diff.iloc[i]):
            continue

        d = diff.iloc[i]
        prev_dir = direction.iloc[i - 1] if i > 4 else 0

        if d < 0:          # 买入条件：今 < 4天前
            if prev_dir <= 0:
                # 新买入序列启动（prev_dir<=0 时，count 强制重置到 0 后+1=1）
                # 注意：prev_dir==1 说明刚中断卖出序列，需先清零再启动买入
                count.iloc[i] = 1
                direction.iloc[i] = 1
            else:
                # 当前已是买入序列，延续计数，到9后归零重计
                count.iloc[i] = min(count.iloc[i - 1] + 1, 9)
                direction.iloc[i] = 1

        elif d > 0:         # 卖出条件：今 > 4天前
            if prev_dir >= 0:
                # 新卖出序列启动（prev_dir>=0 时，count 强制重置到 0 后-1=-1）
                # 注意：prev_dir==1 说明刚中断买入序列，需先清零再启动卖出
                count.iloc[i] = -1
                direction.iloc[i] = -1
            else:
                # 当前已是卖出序列，延续计数，到-9后归零重计
                count.iloc[i] = max(count.iloc[i - 1] - 1, -9)
                direction.iloc[i] = -1

        else:               # 中性，重置
            count.iloc[i] = 0
            direction.iloc[i] = 0

    # ── 完美Bar校验：买入9要求收盘 < 前1根最低，卖出9要求收盘 > 前1根最高 ──
    if len(close) > 4:
        for i in range(4, len(close)):
            if count.iloc[i] == 9:
                bar9_close = close.iloc[i]
                # 优先用 low 参数，严格校验；无 low 时降级为前一根收盘
                bar8_low = low.iloc[i - 1] if low is not None else close.iloc[i - 1]
                if bar9_close >= bar8_low:
                    count.iloc[i] = 8
                    direction.iloc[i] = 1
            elif count.iloc[i] == -9:
                bar9_close = close.iloc[i]
                bar8_high = high.iloc[i - 1] if high is not None else close.iloc[i - 1]
                if bar9_close <= bar8_high:
                    count.iloc[i] = -8
                    direction.iloc[i] = -1

    return count


def calc_skdj(
    close: pd.Series,
    high: pd.Series,
    low: pd.Series,
    n: int = 9,
    m: int = 3
) -> Tuple[pd.Series, pd.Series]:
    """
    计算 SKDJ 指标（慢速随机指标）
    返回: (SK, SD)
    """
    ll = low.rolling(window=n).min()
    hh = high.rolling(window=n).max()
    rsv = (close - ll) / (hh - ll) * 100
    rsv = rsv.fillna(50)

    # 向量化 EMA：递推公式 sk[i] = (1-1/m)*sk[i-1] + (1/m)*rsv[i]
    # pandas ewm(span) 中 alpha = 2/(span+1)，令 alpha = 1/m 得 span = 2*m - 1
    span = 2 * m - 1
    sk = rsv.ewm(span=span, adjust=False).mean()
    sd = sk.ewm(span=span, adjust=False).mean()

    return sk, sd


def calc_risk_flags(
    close: pd.Series,
    high: pd.Series,
    low: pd.Series,
    vol: pd.Series,
    pct_chg: pd.Series,
) -> list:
    """
    根据最新K线数据计算风险标签列表。
    返回结构: [{"type": str, "label": str, "level": str, "desc": str}, ...]
    level: "warn"(黄) / "danger"(红)
    """
    flags = []

    if len(close) < 10:
        return flags

    # ── 指标计算 ──────────────────────────────────────────
    dif, dea, macd_bar = calc_macd(close)
    rsi = calc_rsi(close, 14)
    upper, mid, lower = calc_bollinger(close)
    ma_dict = calc_ma(close)
    vol_ratio = calc_volume_ratio(vol)
    td_count = td_sequential_count(close, high=high, low=low)

    # 最新值
    latest = float(close.iloc[-1])
    rsi_latest = float(rsi.iloc[-1])
    dif_latest = float(dif.iloc[-1])
    dea_latest = float(dea.iloc[-1])
    upper_latest = float(upper.iloc[-1])
    lower_latest = float(lower.iloc[-1])
    vol_r_latest = float(vol_ratio.iloc[-1])
    td_latest = int(td_count.iloc[-1])
    pct_latest = float(pct_chg.iloc[-1]) if pct_chg is not None and len(pct_chg) > 0 else 0.0

    # 昨日值
    prev_dif = float(dif.iloc[-2])
    prev_dea = float(dea.iloc[-2])

    ma5_s  = ma_dict.get("ma5")
    ma10_s = ma_dict.get("ma10")
    ma20_s = ma_dict.get("ma20")
    ma5  = float(ma5_s.iloc[-1])  if ma5_s  is not None and len(ma5_s)  > 0 else 0.0
    ma10 = float(ma10_s.iloc[-1]) if ma10_s is not None and len(ma10_s) > 0 else 0.0
    ma20 = float(ma20_s.iloc[-1]) if ma20_s is not None and len(ma20_s) > 0 else 0.0

    # ── RSI 超买/超卖（阈值与卖出分析器统一）─────────────────────
    if rsi_latest >= 75:
        flags.append({
            "type": "rsi_overbought",
            "label": "RSI超买",
            "level": "danger",
            "desc": f"RSI(14)={rsi_latest:.0f}，严重超买，注意止盈/回调风险",
        })
    elif rsi_latest >= 65:
        flags.append({
            "type": "rsi_warm",
            "label": "RSI偏热",
            "level": "warn",
            "desc": f"RSI(14)={rsi_latest:.0f}，偏热区域，警惕反转",
        })
    elif rsi_latest <= 20:
        flags.append({
            "type": "rsi_oversold",
            "label": "RSI超卖",
            "level": "warn",
            "desc": f"RSI(14)={rsi_latest:.0f}，超卖区域，可能存在反弹机会",
        })

    # ── MACD 死叉 ─────────────────────────────────────────
    if prev_dif > prev_dea and dif_latest < dea_latest:
        flags.append({
            "type": "macd_death_cross",
            "label": "MACD死叉",
            "level": "danger",
            "desc": "MACD 快线向下穿越慢线，短期趋势转空",
        })

    # ── MACD 顶背离 ────────────────────────────────────────
    if len(close) >= 20:
        recent_high = float(high.iloc[-20:].max())
        macd_high = float(dif.iloc[-20:].max())
        if latest >= recent_high * 0.99 and dif_latest < macd_high * 0.95:
            flags.append({
                "type": "macd_top_div",
                "label": "MACD顶背离",
                "level": "danger",
                "desc": "价格创新高但MACD未跟随，动能衰竭预警",
            })

    # ── RSI 顶背离（卖出分析器同逻辑）────────────────────────
    if len(rsi) >= 20:
        rsi_high = float(rsi.iloc[-20:].max())
        price_high = float(close.iloc[-20:].max())
        if rsi_latest < rsi_high * 0.95 and latest >= price_high * 0.99:
            flags.append({
                "type": "rsi_divergence",
                "label": "RSI顶背离",
                "level": "danger",
                "desc": "价格新高但RSI未跟随，上涨动能减弱",
            })

    # ── 高位滞涨（止盈信号）─────────────────────────────────
    if len(pct_chg) >= 15:
        recent_5pct = float(pct_chg.iloc[-5:].sum())
        prev_10pct = float(pct_chg.iloc[-15:-5].sum())
        if recent_5pct < 2 and prev_10pct > 10:
            flags.append({
                "type": "stagnation_high",
                "label": "高位滞涨",
                "level": "warn",
                "desc": f"高位滞涨：近5日仅涨{recent_5pct:.1f}%，前期大涨{prev_10pct:.1f}%，建议止盈",
            })

    # ── 布林带（接近阈值与卖出分析器统一为-5%）─────────────────
    if upper_latest > 0:
        upper_pct = (latest - upper_latest) / upper_latest * 100
        if upper_pct >= 0:
            flags.append({
                "type": "bollinger_upper",
                "label": "布林上轨",
                "level": "danger",
                "desc": "触及布林上轨，均值回归概率大，建议止盈",
            })
        elif upper_pct >= -5:
            flags.append({
                "type": "bollinger_near",
                "label": "接近布林上轨",
                "level": "warn",
                "desc": f"距离布林上轨仅 {-upper_pct:.1f}%，注意压力",
            })
        elif lower_latest > 0 and latest <= lower_latest:
            flags.append({
                "type": "bollinger_lower",
                "label": "布林下轨",
                "level": "warn",
                "desc": "触及布林下轨，可能存在反弹机会",
            })

    # ── TD 九转卖出 ────────────────────────────────────────
    if td_latest <= -9:
        flags.append({
            "type": "td_sell",
            "label": "TD九转卖出",
            "level": "danger",
            "desc": f"TD九转计数={abs(td_latest)}，强烈卖出信号",
        })
    elif td_latest <= -6:
        flags.append({
            "type": "td_sell_early",
            "label": "TD九转进行中",
            "level": "warn",
            "desc": f"TD九转计数={abs(td_latest)}，接近成熟，注意变盘",
        })

    # ── 量价背离 ──────────────────────────────────────────
    if len(vol) >= 6:
        avg_vol_5 = float(vol.iloc[-6:-1].mean())
        today_vol = float(vol.iloc[-1])
        if avg_vol_5 > 0 and pct_latest > 1 and today_vol < avg_vol_5 * 0.7:
            flags.append({
                "type": "volume_div",
                "label": "量价背离",
                "level": "warn",
                "desc": "上涨但缩量，上涨动力不足",
            })

    # ── 均线空头排列 ───────────────────────────────────────
    if ma5 < ma10 < ma20 and latest < ma5:
        flags.append({
            "type": "ma_empty",
            "label": "均线空头",
            "level": "danger",
            "desc": "5/10/20均线空头排列，中期趋势向下",
        })

    # ── 跌破重要均线 ───────────────────────────────────────
    if latest < ma5 and ma5 > 0:
        flags.append({
            "type": "below_ma5",
            "label": "跌破5日线",
            "level": "warn",
            "desc": "价格跌破5日均线，短期走弱",
        })

    return flags
