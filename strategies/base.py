"""
策略基类定义
所有选股策略继承自 BaseStrategy
"""

import pandas as pd
import numpy as np
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Callable
import logging

logger = logging.getLogger(__name__)


@dataclass
class StockSignal:
    """单只股票的策略信号"""
    ts_code: str              # 股票代码
    name: str                 # 股票名称
    strategy: str             # 策略名称
    score: float              # 策略评分 (0-100)
    win_rate: float           # 预期胜率 (0-1)
    signals: List[str]        # 触发的信号描述
    latest_price: float       # 最新价格
    pct_chg: float            # 当日涨幅
    volume_ratio: float = 1.0 # 量比
    trade_date: str = ""      # 信号日期
    risk_flags: List[dict] = field(default_factory=list)  # 风险标签列表
    extra: Dict = field(default_factory=dict)  # 附加信息


@dataclass
class ScreenResult:
    """策略筛选结果"""
    strategy_name: str
    strategy_desc: str
    signals: List[StockSignal]
    trade_date: str
    total_scanned: int
    all_signals: List[StockSignal] = field(default_factory=list)  # 全部命中（供合并用）


class BaseStrategy(ABC):
    """
    选股策略基类
    子类需要实现 screen() 方法
    """

    name: str = "base"
    description: str = ""
    base_win_rate: float = 0.5

    def __init__(self, top_n: int = 10):
        self.top_n = top_n
        self._progress_callback: Optional[Callable[[str, int, int], None]] = None

    def set_progress_callback(self, callback: Optional[Callable[[str, int, int], None]]):
        """设置进度回调 (phase, scanned, total)"""
        self._progress_callback = callback

    def _report_progress(self, phase: str, scanned: int, total: int):
        """子类在扫描循环中调用，报告当前进度"""
        if self._progress_callback:
            self._progress_callback(phase, scanned, total)

    @abstractmethod
    def screen(
        self,
        stock_list: pd.DataFrame,
        scanner=None
    ) -> ScreenResult:
        """
        筛选股票
        Args:
            stock_list: 股票列表 DataFrame (ts_code, name, ...)
            scanner: MarketScanner 实例
        Returns:
            ScreenResult
        """
        pass

    def _calc_win_rate(self, score: float, signals: List[str]) -> float:
        """根据评分和信号数量估算胜率"""
        base = self.base_win_rate
        score_bonus = (score - 50) / 100 * 0.2
        signal_bonus = min(len(signals) * 0.03, 0.15)
        win_rate = base + score_bonus + signal_bonus
        return round(max(0.3, min(0.85, win_rate)), 3)

    def _get_name_map(self, stock_list: pd.DataFrame) -> Dict[str, str]:
        """构建 代码 -> name 映射（过滤ST股）"""
        if stock_list.empty:
            return {}
        code_col = "代码" if "代码" in stock_list.columns else \
                   "ts_code" if "ts_code" in stock_list.columns else None
        name_col = "名称" if "名称" in stock_list.columns else "name"
        if code_col and name_col in stock_list.columns:
            valid = ~stock_list[name_col].str.contains("ST|退", na=False)
            df_clean = stock_list[valid]
            return dict(zip(df_clean[code_col].astype(str), df_clean[name_col].astype(str)))
        return {}

    def _get_codes(self, stock_list: pd.DataFrame) -> List[str]:
        """从股票列表提取代码"""
        code_col = "代码" if "代码" in stock_list.columns else "ts_code"
        if stock_list.empty or code_col not in stock_list.columns:
            return []
        return stock_list[code_col].astype(str).tolist()

    def _get_quote(self, scanner, code: str, default_price: float = 0.0) -> dict:
        """获取实时行情，带异常兜底"""
        try:
            return scanner.get_realtime(code)
        except Exception:
            return {"涨跌幅": 0.0, "最新价": default_price, "换手率": 0.0}

    def _build_result(self, candidates: List[StockSignal], trade_date: str,
                      scanned: int, sort_key=None) -> ScreenResult:
        """统一构造筛选结果：signals 为 Top N 展示用，all_signals 为全量命中供合并用"""
        key = sort_key or (lambda x: x.score)
        candidates.sort(key=key, reverse=True)
        return ScreenResult(
            strategy_name=self.name,
            strategy_desc=self.description,
            signals=candidates[:self.top_n],
            trade_date=trade_date,
            total_scanned=scanned,
            all_signals=candidates[:],
        )


def _compute_risk_flags(df: pd.DataFrame) -> list:
    """
    从 K线 DataFrame 计算风险标签。
    各策略通用辅助函数，避免重复代码。
    """
    try:
        from ..utils.indicators import calc_risk_flags
    except ImportError:
        return []
    if df is None or len(df) < 10:
        return []
    close = df["close"].astype(float)
    high  = df["high"].astype(float)
    low   = df["low"].astype(float)
    vol   = df["vol"].astype(float)
    pct_col = "pct_chg" if "pct_chg" in df.columns else "daily_chg"
    pct_chg = df[pct_col].astype(float) if pct_col in df.columns else pd.Series(0, index=close.index)
    return calc_risk_flags(close, high, low, vol, pct_chg)
