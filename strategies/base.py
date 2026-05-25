"""
策略基类定义
所有选股策略继承自 BaseStrategy
采用模板方法模式：子类只需实现 _evaluate_single_stock()，
基类提供通用 screen() 实现（内部20线程并行遍历）。
"""

import pandas as pd
import numpy as np
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed
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
    选股策略基类（模板方法模式）
    子类需要实现 _evaluate_single_stock() 方法
    基类提供通用的 screen() 并行遍历实现
    """

    name: str = "base"
    description: str = ""
    base_win_rate: float = 0.5

    def __init__(self, top_n: int = 20):
        self.top_n = top_n
        self._progress_callback: Optional[Callable[[str, int, int], None]] = None

    def set_progress_callback(self, callback: Optional[Callable[[str, int, int], None]]):
        """设置进度回调 (phase, scanned, total)"""
        self._progress_callback = callback

    def _report_progress(self, phase: str, scanned: int, total: int):
        """报告当前进度"""
        if self._progress_callback:
            self._progress_callback(phase, scanned, total)

    class _SkipStock(Exception):
        """数据不足或其他原因跳过该股票，不计入 scanned"""
        pass

    @abstractmethod
    def _evaluate_single_stock(
        self,
        code: str,
        scanner,
        name_map: Dict[str, str],
        trade_date: str,
    ) -> Optional[StockSignal]:
        """
        评估单只股票，子类只需关注评分逻辑。

        Args:
            code: 股票代码
            scanner: MarketScanner 实例
            name_map: 代码→名称映射
            trade_date: 交易日期字符串

        Returns:
            StockSignal 如果命中，None 如果不命中但评估正常完成

        Raises:
            self._SkipStock: 数据不足时应抛出，该股票不计入 scanned
        """
        pass

    def screen(
        self,
        stock_list: pd.DataFrame,
        scanner=None,
        max_workers: int = 20,
    ) -> ScreenResult:
        """
        通用并行筛选模板。
        使用线程池并行遍历股票，子类通过 _evaluate_single_stock() 实现评分逻辑。

        Args:
            stock_list: 股票列表 DataFrame
            scanner: MarketScanner 实例
            max_workers: 并行线程数（默认20）

        Returns:
            ScreenResult
        """
        from ..data.fetcher import market_scanner, get_latest_trade_date

        if scanner is None:
            scanner = market_scanner
        scanner.load()
        trade_date = get_latest_trade_date()
        name_map = self._get_name_map(stock_list)
        codes = self._get_codes(stock_list)
        total = len(codes)

        candidates: List[StockSignal] = []
        scanned = 0

        def _eval_one(code: str) -> tuple:
            """评估单只，返回 (signal_or_none, evaluated_flag)"""
            try:
                sig = self._evaluate_single_stock(code, scanner, name_map, trade_date)
                return sig, True
            except self._SkipStock:
                return None, False
            except Exception as e:
                logger.debug(f"[{self.name}] {code} 计算失败: {e}")
                return None, False

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(_eval_one, c): c for c in codes}
            for idx, future in enumerate(as_completed(futures), 1):
                sig, evaluated = future.result()
                if evaluated:
                    scanned += 1
                if sig is not None:
                    candidates.append(sig)
                self._report_progress("executing", idx, total)

        return self._build_result(candidates, trade_date, scanned)

    @staticmethod
    def _resolve_code_col(stock_list: pd.DataFrame) -> Optional[str]:
        """解析股票列表中的代码列名"""
        if "代码" in stock_list.columns:
            return "代码"
        if "ts_code" in stock_list.columns:
            return "ts_code"
        return None

    @staticmethod
    def _resolve_name_col(stock_list: pd.DataFrame) -> Optional[str]:
        """解析股票列表中的名称列名"""
        if "名称" in stock_list.columns:
            return "名称"
        if "name" in stock_list.columns:
            return "name"
        return None

    def _get_name_map(self, stock_list: pd.DataFrame) -> Dict[str, str]:
        """构建 代码 -> name 映射（过滤ST股）"""
        if stock_list.empty:
            return {}
        code_col = self._resolve_code_col(stock_list)
        name_col = self._resolve_name_col(stock_list)
        if code_col and name_col and name_col in stock_list.columns:
            valid = ~stock_list[name_col].str.contains("ST|退", na=False)
            df_clean = stock_list[valid]
            return dict(zip(df_clean[code_col].astype(str), df_clean[name_col].astype(str)))
        return {}

    def _get_codes(self, stock_list: pd.DataFrame) -> List[str]:
        """从股票列表提取代码"""
        code_col = self._resolve_code_col(stock_list)
        if stock_list.empty or code_col is None or code_col not in stock_list.columns:
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
        """统一构造筛选结果：signals 为 Top N 展示用，all_signals 为全量命中供合并用

        优化：
        1. 命中数上限控制（单策略不超过300只）
        2. 排名百分位评分（解决满分扎堆、增加区分度）
        """
        MAX_HITS = 300
        key = sort_key or (lambda x: x.score)
        candidates.sort(key=key, reverse=True)

        # 命中数上限：超过300只时只保留前300
        if len(candidates) > MAX_HITS:
            candidates = candidates[:MAX_HITS]

        # 排名百分位评分：原始分 * 0.6 + 排名加成 * 40
        # 解决多个股票原始分都是100分的问题
        total = len(candidates)
        for rank, sig in enumerate(candidates, 1):
            rank_pct = (rank - 1) / total if total > 1 else 0
            # 排名越靠前，最终分数越高
            # 例：原始100分排名第1 → 60 + 40 = 100
            # 例：原始100分排名50% → 60 + 20 = 80
            # 例：原始80分排名最后 → 48 + 0 = 48
            sig.score = round(sig.score * 0.6 + (1 - rank_pct) * 40, 1)

        candidates.sort(key=lambda x: x.score, reverse=True)
        return ScreenResult(
            strategy_name=self.name,
            strategy_desc=self.description,
            signals=candidates[:self.top_n],
            trade_date=trade_date,
            total_scanned=scanned,
            all_signals=candidates[:],
        )


def _resolve_pct_col(df: pd.DataFrame) -> str:
    """解析K线DataFrame中的涨跌幅列名"""
    return "pct_chg" if "pct_chg" in df.columns else "daily_chg"


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
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    vol = df["vol"].astype(float)
    pct_col = _resolve_pct_col(df)
    pct_chg = df[pct_col].astype(float) if pct_col in df.columns else pd.Series(0, index=close.index)
    return calc_risk_flags(close, high, low, vol, pct_chg)
