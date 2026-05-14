"""
卖出信号分析器 — 歌者
对持仓股票进行多维度卖出信号检测，综合评分并给出操作建议。
"""

import pandas as pd
import numpy as np
import logging
from dataclasses import dataclass, field
from typing import List, Dict, Optional

from ..utils.indicators import (
    calc_macd, calc_rsi, calc_bollinger,
    calc_ma, calc_volume_ratio, td_sequential_count,
    calc_risk_flags
)

logger = logging.getLogger(__name__)

# ── 卖出信号级别 ──────────────────────────────────────────────────────────────
SELL_LEVEL_INFO = {
    "HOLD":    {"label": "继续持有", "icon": "💤", "color": "#8b949e", "action": "hold"},
    "WATCH":   {"label": "关注",     "icon": "👁", "color": "#d29922", "action": "watch"},
    "REDUCE":  {"label": "考虑减仓", "icon": "⚠️", "color": "#ffa657", "action": "reduce"},
    "SELL":    {"label": "建议卖出", "icon": "🔴", "color": "#f85149", "action": "sell"},
    "URGENT":  {"label": "紧急卖出", "icon": "🚨", "color": "#da3633", "action": "urgent_sell"},
}


@dataclass
class SellSignal:
    """单只持仓的卖出信号"""
    code: str
    name: str
    current_price: float
    avg_cost: float
    pnl_pct: float       # 持仓盈亏比例（%）
    hold_days: int       # 持仓天数
    # 信号维度
    signals: List[str] = field(default_factory=list)  # 触发信号列表
    urgent_signals: List[str] = field(default_factory=list)  # 紧急信号（立即触发）
    # 综合评估
    sell_score: int = 0  # 卖出紧迫度 0-100
    sell_level: str = "HOLD"
    sell_level_label: str = "继续持有"
    sell_level_icon: str = "💤"
    sell_level_color: str = "#8b949e"
    # 建议
    action: str = "hold"
    reason: str = ""      # 主要理由
    # 技术指标快照
    rsi14: float = 0.0
    macd_state: str = ""  # "金叉"/"死叉"/"零轴上死叉"/"零轴下金叉"
    bollinger_pos: str = ""  # "上轨"/"中轨"/"下轨"
    trend: str = ""      # "多头"/"空头"/"震荡"
    # 风险扫描（复用 calc_risk_flags）
    risk_tag: str = "safe"       # safe | watch | conflict | high_risk
    risk_score: int = 0          # 0-100
    risk_flags: List[Dict] = field(default_factory=list)  # [{type, label, level, desc}]


class SellAnalyzer:
    """
    持仓卖出信号分析器

    分析维度：
    1. 止盈信号：RSI超买、触及布林上轨、连续高位滞涨
    2. 止损信号：跌破成本价一定幅度、均线空头排列
    3. 警示信号：量价背离、MACD顶背离、TD九转卖出计数
    4. 综合评分 → 给出 HOLD/WATCH/REDUCE/SELL/URGENT 五级建议
    """

    # 各信号维度的权重（满分100）
    WEIGHTS = {
        # 止损维度（权重高，紧急）
        "stop_loss_big":    40,   # 跌幅>10% 止损
        "stop_loss_mid":    20,   # 跌幅>5% 警示
        "stop_loss_small":  8,    # 跌破5日线
        "stop_loss_medium": 15,   # 跌破20日线

        # 止盈维度
        "rsi_overbought":   25,   # RSI>75 超买
        "rsi_warm":         12,   # RSI>65 偏热
        "bollinger_upper":   20,   # 触及布林上轨
        "bollinger_near":    10,   # 接近布林上轨（±5%）
        "stagnation_high":   15,   # 高位滞涨（涨不动）

        # 警示维度
        "macd_death_cross": 15,   # MACD死叉
        "macd_top_div":     20,   # MACD顶背离
        "volume_price_div": 12,   # 量价背离（涨缩量）
        "td_sell_count":     18,   # TD九转卖出计数=9
        "rsi_divergence":    15,   # RSI顶背离

        # 持仓时长维度
        "hold_long_win":    -5,   # 持仓>20天盈利，建议适度止盈
        "hold_long_loss":    10,   # 持仓>20天亏损，补仓逻辑不同
    }

    def __init__(self, lookback_days: int = 60):
        self.lookback_days = lookback_days

    def analyze_position(
        self,
        code: str,
        name: str,
        avg_cost: float,
        current_price: float,
        buy_date: str,
        history: pd.DataFrame
    ) -> SellSignal:
        """
        分析单只持仓的卖出信号
        Args:
            code: 股票代码
            name: 股票名称
            avg_cost: 持仓成本
            current_price: 当前价格
            buy_date: 买入日期 "YYYY-MM-DD"
            history: 历史K线 DataFrame，需包含 high/low/open/close/vol/daily_chg
        Returns:
            SellSignal 对象
        """
        if history.empty or len(history) < 10:
            return self._default_signal(code, name, avg_cost, current_price, buy_date)

        from datetime import datetime, date
        try:
            hold_days = (datetime.now().date() - datetime.strptime(buy_date, "%Y-%m-%d").date()).days
        except (ValueError, TypeError):
            hold_days = 0

        signal = SellSignal(
            code=code,
            name=name,
            current_price=current_price,
            avg_cost=avg_cost,
            pnl_pct=round((current_price - avg_cost) / avg_cost * 100, 2),
            hold_days=hold_days,
        )

        # ── 计算技术指标 ──────────────────────────────────────────────
        close = history["close"].astype(float)
        high  = history["high"].astype(float)
        low   = history["low"].astype(float)
        open_ = history["open"].astype(float)
        vol   = history["vol"].astype(float)

        # 处理日涨跌幅（可能是 pct_chg 列，也可能是 daily_chg）
        pct_col = "pct_chg" if "pct_chg" in history.columns else "daily_chg"
        pct_chg = history[pct_col].astype(float) if pct_col in history.columns else pd.Series(0, index=close.index)

        dif, dea, macd_bar = calc_macd(close)
        rsi = calc_rsi(close, 14)
        upper, mid, lower = calc_bollinger(close)
        ma_dict = calc_ma(close)
        vol_ratio = calc_volume_ratio(vol)

        # 最新数据
        latest = close.iloc[-1]
        rsi_latest = rsi.iloc[-1]
        dif_latest = dif.iloc[-1]
        dea_latest = dea.iloc[-1]
        macd_latest = macd_bar.iloc[-1]
        upper_latest = upper.iloc[-1]
        lower_latest = lower.iloc[-1]
        vol_r_latest = vol_ratio.iloc[-1]
        ma5  = ma_dict.get("ma5", pd.Series(0, index=close.index)).iloc[-1]
        ma10 = ma_dict.get("ma10", pd.Series(0, index=close.index)).iloc[-1]
        ma20 = ma_dict.get("ma20", pd.Series(0, index=close.index)).iloc[-1]
        ma60 = ma_dict.get("ma60", pd.Series(0, index=close.index)).iloc[-1]

        # ── 指标快照 ─────────────────────────────────────────────────
        signal.rsi14 = round(rsi_latest, 1)
        signal.trend = self._detect_trend(close, ma5, ma10, ma20)
        signal.bollinger_pos = self._bollinger_position(latest, upper_latest, mid.iloc[-1], lower_latest)
        signal.macd_state = self._macd_state(dif_latest, dea_latest, dif.iloc[-2], dea.iloc[-2])

        score = 0
        urgent_signals = []
        watch_signals = []

        pnl_pct = signal.pnl_pct

        # ══════════════════════════════════════════════════════════════
        # 维度1：止损检查（权重最高，持仓特有）
        # ══════════════════════════════════════════════════════════════
        if pnl_pct <= -10:
            score += self.WEIGHTS["stop_loss_big"]
            urgent_signals.append(f"跌幅达 {abs(pnl_pct):.1f}%，触发止损线")
        elif pnl_pct <= -5:
            score += self.WEIGHTS["stop_loss_mid"]
            watch_signals.append(f"浮亏 {abs(pnl_pct):.1f}%，需关注")

        # 均线止损检查
        if latest < ma5:
            score += self.WEIGHTS["stop_loss_small"]
            watch_signals.append("价格跌破5日均线，短期走弱")
        if latest < ma20:
            score += self.WEIGHTS["stop_loss_medium"]
            watch_signals.append("价格跌破20日均线，中期趋势转空")
        if ma5 < ma10:
            score += 5
            watch_signals.append("均线空头排列（MA5<MA10）")

        # ══════════════════════════════════════════════════════════════
        # 维度2&3：复用 calc_risk_flags 的警示/止盈信号
        # ══════════════════════════════════════════════════════════════
        risk_flags = calc_risk_flags(close, high, low, vol, pct_chg)
        # calc_risk_flags 的 type -> 卖出分析器权重映射
        RISK_WEIGHT_MAP = {
            "rsi_overbought": 25,
            "rsi_warm": 12,
            "macd_death_cross": 15,
            "macd_top_div": 20,
            "rsi_divergence": 15,
            "bollinger_upper": 20,
            "bollinger_near": 10,
            "stagnation_high": 15,
            "td_sell": 18,
            "td_sell_early": 10,
            "volume_div": 12,
            # 以下在止损维度已覆盖，跳过避免重复计分
            # "ma_empty": 0,
            # "below_ma5": 0,
        }
        for f in risk_flags:
            w = RISK_WEIGHT_MAP.get(f["type"], 0)
            if w == 0:
                continue
            score += w
            if f["level"] == "danger":
                urgent_signals.append(f["desc"])
            else:
                watch_signals.append(f["desc"])

        # 同步计算风险标签（与选股系统一致）
        signal.risk_flags = risk_flags
        risk_score = sum(25 if f["level"] == "danger" else 12 for f in risk_flags)
        risk_score = min(risk_score, 100)
        signal.risk_score = risk_score
        if risk_score >= 50:
            signal.risk_tag = "high_risk"
        elif risk_score >= 28:
            signal.risk_tag = "conflict"
        elif risk_score >= 12:
            signal.risk_tag = "watch"
        else:
            signal.risk_tag = "safe"

        # ══════════════════════════════════════════════════════════════
        # 维度4：持仓时长（持仓特有）
        # ══════════════════════════════════════════════════════════════
        if hold_days > 20 and pnl_pct > 5:
            score += self.WEIGHTS["hold_long_win"]
            watch_signals.append(f"持仓{hold_days}天盈利，建议适度止盈保护利润")
        if hold_days > 30 and pnl_pct < -3:
            score += self.WEIGHTS["hold_long_loss"]
            watch_signals.append(f"持仓{hold_days}天仍亏损，建议重新评估")

        # ══════════════════════════════════════════════════════════════
        # 综合评分 → 级别
        # ══════════════════════════════════════════════════════════════
        score = max(0, min(100, score))
        signal.sell_score = score
        signal.urgent_signals = urgent_signals
        signal.signals = watch_signals

        if score >= 65:
            level = "URGENT"
        elif score >= 45:
            level = "SELL"
        elif score >= 25:
            level = "REDUCE"
        elif score >= 10:
            level = "WATCH"
        else:
            level = "HOLD"

        info = SELL_LEVEL_INFO[level]
        signal.sell_level = level
        signal.sell_level_label = info["label"]
        signal.sell_level_icon = info["icon"]
        signal.sell_level_color = info["color"]
        signal.action = info["action"]

        # 主要理由（取最高权重信号）
        all_signals = urgent_signals + watch_signals
        if all_signals:
            signal.reason = all_signals[0]

        return signal

    def _default_signal(self, code, name, avg_cost, current_price, buy_date) -> SellSignal:
        """K线数据不足时返回默认信号"""
        from datetime import datetime
        try:
            hold_days = (datetime.now().date() - datetime.strptime(buy_date, "%Y-%m-%d").date()).days
        except (ValueError, TypeError):
            hold_days = 0
        pnl_pct = round((current_price - avg_cost) / avg_cost * 100, 2)
        return SellSignal(
            code=code, name=name,
            current_price=current_price, avg_cost=avg_cost,
            pnl_pct=pnl_pct, hold_days=hold_days,
            sell_level="HOLD",
            sell_level_label="继续持有",
            sell_level_icon="💤",
            sell_level_color="#8b949e",
            action="hold",
            reason="K线数据不足，无法完整分析"
        )

    def _detect_trend(self, close: pd.Series, ma5: float, ma10: float, ma20: float) -> str:
        """判断趋势方向"""
        if close.iloc[-1] > ma5 and ma5 > ma10 and ma10 > ma20:
            return "多头排列"
        elif close.iloc[-1] < ma5 and ma5 < ma10 and ma10 < ma20:
            return "空头排列"
        elif close.iloc[-1] > ma20:
            return "震荡偏多"
        else:
            return "震荡偏空"

    def _bollinger_position(self, price: float, upper: float, mid: float, lower: float) -> str:
        """价格在布林带的位置"""
        if upper > 0 and price >= upper:
            return "上轨上方"
        elif upper > 0 and price >= mid:
            return "上半段"
        elif upper > 0 and price >= lower:
            return "下半段"
        else:
            return "下轨下方"

    def _macd_state(self, dif: float, dea: float, prev_dif: float, prev_dea: float) -> str:
        """MACD 状态描述"""
        if dif > dea and prev_dif <= prev_dea:
            return "金叉"
        elif dif < dea and prev_dif >= prev_dea:
            return "死叉"
        elif dif > 0 and dea > 0:
            return "零轴上方"
        elif dif < 0 and dea < 0:
            return "零轴下方"
        elif dif > 0:
            return "零轴上运行"
        else:
            return "零轴下运行"


# ── 全局单例 ─────────────────────────────────────────────────────────────────
_analyzer: Optional[SellAnalyzer] = None


def get_analyzer() -> SellAnalyzer:
    global _analyzer
    if _analyzer is None:
        _analyzer = SellAnalyzer()
    return _analyzer
