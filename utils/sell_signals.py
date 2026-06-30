# -*- coding: utf-8 -*-
"""
卖出信号检测工具

提供统一的卖出信号检测逻辑，可被：
1. 持仓卖出决策（生成卖出信号）
2. 买入风险评估（卖出信号强 = 买入风险高）
"""
import pandas as pd
import logging

logger = logging.getLogger(__name__)

# [修复3] 从 indicators.py 直接导入，避免重复代码
from .indicators import calc_macd, calc_rsi


def detect_sell_signals(df: pd.DataFrame) -> dict:
    """
    检测卖出信号
    
    Args:
        df: K线数据，必须包含 open, high, low, close, vol 列
        
    Returns:
        {
            "has_sell_signal": bool,      # 是否有卖出信号
            "sell_signals": List[str],    # 卖出信号列表
            "sell_score": int,            # 卖出评分（越高越应该卖出）
            "stop_loss_price": float,      # 建议止损价
            "take_profit_price": float,    # 建议止盈价（如果已持仓）
            "risk_level": str,             # 风险等级 (low/medium/high)
        }
    """
    if df is None or df.empty or len(df) < 20:
        return {
            "has_sell_signal": False,
            "sell_signals": [],
            "sell_score": 0,
            "stop_loss_price": None,
            "take_profit_price": None,
            "risk_level": "unknown",
        }
    
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    vol = df["vol"].astype(float)
    
    sell_signals = []
    sell_score = 0
    
    # [修复3] 直接使用 indicators.py 的实现
    dif, dea, macd_bar = calc_macd(close)
    if len(dif) >= 2:
        if dif.iloc[-1] < dea.iloc[-1] and dif.iloc[-2] >= dea.iloc[-2]:
            sell_signals.append("MACD死叉")
            sell_score += 25
        elif dif.iloc[-1] < 0 and dif.iloc[-2] >= 0:
            sell_signals.append("MACD跌破零轴")
            sell_score += 30
    
    # ── 信号2: MA死叉 + 空头排列 ──
    ma5 = close.rolling(5).mean()
    ma10 = close.rolling(10).mean()
    ma20 = close.rolling(20).mean()
    ma60 = close.rolling(60).mean()
    
    if len(ma5) >= 2:
        # MA5死叉MA10
        if ma5.iloc[-1] < ma10.iloc[-1] and ma5.iloc[-2] >= ma10.iloc[-2]:
            sell_signals.append("MA5死叉MA10")
            sell_score += 20
        
        # 跌破MA20
        if close.iloc[-1] < ma20.iloc[-1] and close.iloc[-2] >= ma20.iloc[-2]:
            sell_signals.append("跌破MA20")
            sell_score += 25
        
        # 空头排列（MA5 < MA10 < MA20 < MA60）
        if len(ma60) >= 60:
            if ma5.iloc[-1] < ma10.iloc[-1] < ma20.iloc[-1] < ma60.iloc[-1]:
                sell_signals.append("空头排列")
                sell_score += 35  # 最强卖出信号
    
    # ── 信号3: 高位放量下跌 ──
    if len(vol) >= 5:
        vol_ma5 = vol.rolling(5).mean()
        if not pd.isna(vol_ma5.iloc[-1]):
            vol_ratio = vol.iloc[-1] / vol_ma5.iloc[-1]
            price_change = (close.iloc[-1] - close.iloc[-2]) / close.iloc[-2]
            
            if vol_ratio > 2.0 and price_change < -0.03:
                sell_signals.append("高位放量下跌")
                sell_score += 30
    
    # ── 信号4: 连续下跌 ──
    if len(close) >= 3:
        if close.iloc[-1] < close.iloc[-2] < close.iloc[-3]:
            sell_signals.append("连续3日下跌")
            sell_score += 15
    
    # [修复3] 直接使用 indicators.py 的实现
    rsi = calc_rsi(close)
    if len(rsi) >= 2:
        if rsi.iloc[-2] > 70 and rsi.iloc[-1] < rsi.iloc[-2]:
            sell_signals.append("RSI超买回落")
            sell_score += 20
    
    # ── 信号6: 高波动率（持仓风险加大） ──
    if len(close) >= 20:
        daily_ret = close.pct_change().iloc[-20:]
        ann_vol = float(daily_ret.std() * (252 ** 0.5))
        if ann_vol > 0.80:
            sell_signals.append(f"波动率异常({ann_vol*100:.0f}%年化)")
            sell_score += 20

    # ── 信号7: 连板后高开低走（主力出货） ──
    if len(close) >= 3:
        pct_col = "pct_chg" if "pct_chg" in df.columns else "daily_chg"
        if pct_col in df.columns:
            pct_chg = df[pct_col].astype(float)
            has_recent_limit = any(float(pct_chg.iloc[j]) >= 9.5 for j in range(-3, -1))
            if has_recent_limit:
                today_pct = float(pct_chg.iloc[-1])
                upper_shadow = float(high.iloc[-1]) - float(close.iloc[-1])
                body = abs(float(close.iloc[-1]) - float(close.iloc[-2]))
                if today_pct < 0 and upper_shadow > body * 0.5:
                    sell_signals.append("连板后高开低走出货")
                    sell_score += 30

    # ── 计算止损止盈价 ──
    current_price = close.iloc[-1]
    
    # 止损价：近期低点 - 3% 或 -7%
    recent_low = close.iloc[-20:].min()
    stop_loss_price = round(recent_low * 0.97, 2)  # 近期低点下方3%
    
    # 止盈价：近期高点 + 5% 或 +15%
    recent_high = close.iloc[-20:].max()
    take_profit_price = round(recent_high * 1.05, 2)  # 近期高点上方5%
    
    # ── 风险等级 ──
    if sell_score >= 50:
        risk_level = "high"
    elif sell_score >= 25:
        risk_level = "medium"
    else:
        risk_level = "low"
    
    return {
        "has_sell_signal": len(sell_signals) > 0,
        "sell_signals": sell_signals,
        "sell_score": sell_score,
        "stop_loss_price": stop_loss_price,
        "take_profit_price": take_profit_price,
        "risk_level": risk_level,
    }


def assess_buy_risk(df: pd.DataFrame) -> dict:
    """
    评估买入风险（基于卖出信号）
    
    用于：在生成买入推荐时，评估该股票是否已有强卖出信号
    如果有，则降低推荐优先级或提高风险提示
    
    Returns:
        {
            "buy_risk": str,        # 买入风险 (low/medium/high)
            "risk_reasons": List[str],
            "adjustment": int,       # 评分调整（负数）
        }
    """
    sell_info = detect_sell_signals(df)
    
    if sell_info["risk_level"] == "high":
        return {
            "buy_risk": "high",
            "risk_reasons": sell_info["sell_signals"],
            "adjustment": -20,  # 大幅降低评分
        }
    elif sell_info["risk_level"] == "medium":
        return {
            "buy_risk": "medium",
            "risk_reasons": sell_info["sell_signals"],
            "adjustment": -10,  # 适度降低评分
        }
    else:
        return {
            "buy_risk": "low",
            "risk_reasons": [],
            "adjustment": 0,
        }
