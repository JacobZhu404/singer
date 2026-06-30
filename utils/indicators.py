"""
技术指标计算工具库
所有指标基于 pandas DataFrame 计算，不依赖 talib
"""

import pandas as pd
import numpy as np
import logging
from typing import Tuple, Optional

logger = logging.getLogger(__name__)


def get_limit_pct(code: str, name: Optional[str] = None) -> float:
    """根据股票代码与名称返回当日涨停百分比阈值。

    主板（沪 60xxxx / 深 00xxxx）±10%；ST/*ST ±5%。
    创业板（300xxx / 301xxx）±20%。
    科创板（688xxx）±20%。
    北交所（4xxxxx / 8xxxxx，且非 60/00 开头）±30%。
    """
    code = (code or "").lstrip()
    code6 = code.split(".")[0].zfill(6) if "." in code else code.zfill(6)
    is_st = bool(name) and ("ST" in name.upper() or "*ST" in name.upper())

    if code6.startswith(("300", "301")):
        return 20.0
    if code6.startswith("688"):
        return 20.0
    if code6.startswith(("4", "8")) and not code6.startswith(("60", "00")):
        return 30.0
    return 5.0 if is_st else 10.0


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
    d = close.diff().to_numpy()
    nan_mask = np.isnan(d)
    gain_arr = np.where(d > 0, d, 0.0)
    loss_arr = np.where(d < 0, -d, 0.0)
    # 保留首根 NaN（与 Series.clip 一致），确保 ewm(adjust=False) 的种子点不变
    gain_arr[nan_mask] = np.nan
    loss_arr[nan_mask] = np.nan
    gain = pd.Series(gain_arr, index=close.index)
    loss = pd.Series(loss_arr, index=close.index)
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
    # 填充 NaN：用向前填充（用之前的有效值）
    vol = vol.ffill().bfill()
    avg_vol = vol.rolling(period, min_periods=1).mean()
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
    n = len(close)
    close_arr = close.to_numpy(dtype=float)
    diff_arr = close_arr - np.concatenate([np.full(4, np.nan), close_arr[:-4]]) if n > 4 \
        else np.full(n, np.nan)
    count_arr = np.zeros(n, dtype=np.int64)

    # ── 主递推：count[i] 依赖 count[i-1]/direction[i-1]，无法向量化，但用 numpy 标量索引 ──
    prev_dir = 0
    prev_count = 0
    for i in range(4, n):
        d = diff_arr[i]
        if np.isnan(d):
            prev_dir = 0
            prev_count = 0
            continue

        if d < 0:          # 买入条件：今 < 4天前
            if prev_dir <= 0:
                # 新买入序列启动（prev_dir<=0 时强制重置后 +1=1）
                cur_count, cur_dir = 1, 1
            else:
                # 延续买入计数，封顶 9
                cur_count, cur_dir = (prev_count + 1 if prev_count < 9 else 9), 1
        elif d > 0:         # 卖出条件：今 > 4天前
            if prev_dir >= 0:
                cur_count, cur_dir = -1, -1
            else:
                cur_count, cur_dir = (prev_count - 1 if prev_count > -9 else -9), -1
        else:               # 中性，重置
            cur_count, cur_dir = 0, 0

        count_arr[i] = cur_count
        prev_dir = cur_dir
        prev_count = cur_count

    # ── 完美Bar校验（向量化）：买入9要求收盘 < 前1根最低，卖出9要求收盘 > 前1根最高 ──
    if n > 4:
        low_ref = (low.to_numpy(dtype=float) if low is not None else close_arr)
        high_ref = (high.to_numpy(dtype=float) if high is not None else close_arr)
        bar8_low = np.concatenate([close_arr[:1], low_ref[:-1]])   # 前1根最低（i=0 降级为自身收盘）
        bar8_high = np.concatenate([close_arr[:1], high_ref[:-1]])
        # 买入9不完美（收盘≥前1根最低）→ 回到 8
        count_arr[(count_arr == 9) & (close_arr >= bar8_low)] = 8
        # 卖出9不完美（收盘≤前1根最高）→ 回到 -8
        count_arr[(count_arr == -9) & (close_arr <= bar8_high)] = -8

    return pd.Series(count_arr, index=close.index, dtype=int)


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

    # ── 高波动率风险 ──────────────────────────────────────
    if len(close) >= 20:
        daily_ret = close.pct_change().iloc[-20:]
        ann_vol = float(daily_ret.std() * (252 ** 0.5))
        if ann_vol > 0.80:
            flags.append({
                "type": "high_vol",
                "label": "高波动",
                "level": "danger",
                "desc": f"年化波动率{ann_vol*100:.0f}%，远超正常水平，崩盘风险大",
            })
        elif ann_vol > 0.60:
            flags.append({
                "type": "high_vol",
                "label": "波动偏高",
                "level": "warn",
                "desc": f"年化波动率{ann_vol*100:.0f}%，波动较大，注意仓位控制",
            })

    # ── 连板后高开低走（涨停打开出货）────────────────────────
    if len(pct_chg) >= 3 and len(high) >= 3:
        limit_pct = 9.5
        has_recent_limit = any(float(pct_chg.iloc[j]) >= limit_pct for j in range(-3, -1))
        if has_recent_limit:
            today_open_gap = (float(close.iloc[-1]) - float(close.iloc[-2])) / float(close.iloc[-2]) * 100
            today_pct = float(pct_chg.iloc[-1])
            today_upper_shadow = float(high.iloc[-1]) - float(close.iloc[-1])
            today_body = abs(float(close.iloc[-1]) - float(close.iloc[-2]))
            if today_pct < 0 and today_upper_shadow > today_body * 0.5:
                flags.append({
                    "type": "limit_up_dump",
                    "label": "连板后阴线",
                    "level": "danger",
                    "desc": "近期有涨停，今日高开低走收阴，可能主力出货",
                })
            elif today_pct < 2 and float(high.iloc[-1]) > float(close.iloc[-1]) * 1.05:
                flags.append({
                    "type": "limit_up_dump",
                    "label": "涨停后冲高回落",
                    "level": "warn",
                    "desc": "近期有涨停，今日冲高回落，注意出货风险",
                })

    return flags


def is_near_52w_high(high: pd.Series, close: pd.Series, window: int = 250, threshold: float = 0.95) -> bool:
    """
    判断是否接近52周新高（250个交易日）
    
    Args:
        high: 最高价序列
        close: 收盘价序列
        window: 窗口大小（默认250个交易日）
        threshold: 阈值（当前价格 >= 窗口内最高价 * threshold 视为接近新高）
    
    Returns:
        bool: 是否接近52周新高
    """
    if len(high) < window:
        return False
    
    highest = high.iloc[-window:].max()
    current_close = float(close.iloc[-1])
    
    return current_close >= highest * threshold


def calc_relative_strength(close: pd.Series, benchmark_close: pd.Series = None, window: int = 20) -> float:
    """
    计算相对强度（个股涨跌幅 - 基准涨跌幅）
    
    Args:
        close: 个股收盘价序列
        benchmark_close: 基准指数收盘价序列（如沪深300），如果为None则用0作为基准
        window: 计算窗口（默认20日）
    
    Returns:
        float: 相对强度（正数表示跑赢基准）
    """
    if len(close) < window:
        return 0.0
    
    stock_return = (close.iloc[-1] / close.iloc[-window] - 1) * 100
    
    if benchmark_close is not None and len(benchmark_close) >= window:
        benchmark_return = (benchmark_close.iloc[-1] / benchmark_close.iloc[-window] - 1) * 100
        return stock_return - benchmark_return
    
    # 如果没有基准数据，返回个股涨跌幅
    return stock_return


def compute_indicator_bundle(df: pd.DataFrame) -> dict:
    """
    从 K 线 DataFrame 计算策略层通用的指标包（单一事实来源）。

    fetcher.get_indicators（实盘扫描）与回测的 PointInTimeScanner 都调用本函数，
    确保「策略看到的指标」在实盘与回测中完全一致，避免回测逻辑与实盘漂移。

    入参 df 至少需含 close 列；vol 缺失会用 volume 兜底，high/low 缺失用 close 兜底。
    数据不足（<20 行）或计算异常返回 {}。

    返回 dict:
        kline, macd(tuple), rsi, ma(dict), bollinger(dict), vol_ratio, td_count, skdj(tuple)
    """
    if df is None or df.empty or len(df) < 20 or "close" not in df.columns:
        return {}

    close = df["close"]
    vol = df["vol"] if "vol" in df.columns else pd.Series(0, index=df.index)
    if "volume" in df.columns:
        vol = vol.fillna(df["volume"])
    high = df["high"] if "high" in df.columns else close
    low = df["low"] if "low" in df.columns else close

    try:
        macd = calc_macd(close)
        rsi = calc_rsi(close, 14)
        ma = calc_ma(close, [5, 10, 20, 60])
        bb_upper, bb_mid, bb_lower = calc_bollinger(close)
        vr = calc_volume_ratio(vol, 5)
        td = td_sequential_count(close, high=high, low=low)
        sk, sd = calc_skdj(close, high, low)
        return {
            "kline": df,
            "macd": macd,
            "rsi": rsi,
            "ma": ma,
            "bollinger": {"upper": bb_upper, "mid": bb_mid, "lower": bb_lower},
            "vol_ratio": vr,
            "td_count": td,
            "skdj": (sk, sd),
        }
    except Exception as e:
        # 不向上抛出（保持单只异常不打断全市场扫描），但必须可见——之前的 silent except
        # 让指标 bug 完全不可观测；改为 warning + obs 双通道上报
        rows = len(df) if df is not None else 0
        logger.warning(f"compute_indicator_bundle 失败 (rows={rows}): {type(e).__name__}: {e}")
        try:
            from ..core.observability import obs
            obs.error("indicators", "bundle", f"{type(e).__name__}: {e}",
                      context={"rows": rows}, exc=e)
        except Exception:
            pass  # obs 不可用时不阻塞主流程
        return {}
