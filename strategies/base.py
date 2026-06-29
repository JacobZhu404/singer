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


def bar_trade_date(scanner, code: str, fallback: str = "") -> str:
    """返回该股票所用数据**最后一根 bar 的真实日期**（YYYYMMDD）。

    信号日期必须等于产生信号的那根 K 线的日期，而不是 wall-clock `datetime.now()`——
    否则停牌/数据滞后的标的会被贴上"今天"的标签（历史上 000024 招商地产即此问题）。
    所有策略都以最后一根 bar（`iloc[-1]`）作为"当前"，故取其日期即可对齐。
    回测走 PIT scanner 时，最后一根 bar 即 as_of bar，同样自洽。

    只在已命中的少量候选上调用；K 线已在评估阶段缓存，再取一次几乎零成本。
    取不到日期时回退到 `fallback`（调用方传入的 wall-clock 兜底）。
    """
    try:
        df = scanner.get_history(code, days=5)
        if df is not None and len(df) and "date" in df.columns:
            d = pd.to_datetime(df["date"].iloc[-1])
            if pd.notna(d):
                return d.strftime("%Y%m%d")
    except Exception:
        pass
    return fallback


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

    def prepare_for_date(self, scanner, codes, trade_date: str) -> None:
        """在评估单只股票前调用的横截面/全市场预处理 hook。

        默认 no-op。横截面类策略（如 reversal）需要先看到**全部候选股**才能做排名，
        在该 hook 里把结果挂到 scanner 上，evaluate 阶段只做查表。
        - Web/CLI 路径：BaseStrategy.screen() 不直接调用，靠子类自己在 screen() 里编排
          （reversal 现在就是这样）。
        - 回测路径：BacktestEngine 每切换一次 (trade_date, strategy) 都会调用一次，
          以此打通"先全市场排名再逐只评估"的两阶段流程。
        """
        return None

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
        stop_event=None,
    ) -> ScreenResult:
        """
        通用并行筛选模板。
        使用线程池并行遍历股票，子类通过 _evaluate_single_stock() 实现评分逻辑。

        Args:
            stock_list: 股票列表 DataFrame
            scanner: MarketScanner 实例
            max_workers: 并行线程数（默认20）
            stop_event: threading.Event，置位后尽快停止扫描并撒手未完成的 future

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
        errors = 0
        last_err: Optional[str] = None

        def _eval_one(code: str) -> tuple:
            """评估单只，返回 (signal_or_none, evaluated_flag, err_or_none)"""
            # 已收到停止信号的 future 直接短路，不再做计算
            if stop_event is not None and stop_event.is_set():
                return None, False, None
            try:
                sig = self._evaluate_single_stock(code, scanner, name_map, trade_date)
                if sig is not None:
                    # 信号日期对齐到所用数据最后一根 bar 的真实日期，而非 wall-clock
                    sig.trade_date = bar_trade_date(scanner, code, fallback=trade_date)
                return sig, True, None
            except self._SkipStock:
                return None, False, None
            except Exception as e:
                logger.debug(f"[{self.name}] {code} 计算失败: {e}")
                return None, False, f"{type(e).__name__}: {e}"

        # 不用 `with ThreadPoolExecutor() as pool:`：其 __exit__ 会 shutdown(wait=True)，
        # 收到停止信号时仍会卡着等所有在跑的 future 自然结束。改用手动生命周期。
        executor = ThreadPoolExecutor(max_workers=max_workers)
        futures = {}
        try:
            futures = {executor.submit(_eval_one, c): c for c in codes}
            for idx, future in enumerate(as_completed(futures), 1):
                if stop_event is not None and stop_event.is_set():
                    logger.info(f"[{self.name}] 收到停止信号，中止扫描（已处理 {idx}/{total}）")
                    break
                sig, evaluated, err = future.result()
                if evaluated:
                    scanned += 1
                if err is not None:
                    errors += 1
                    last_err = err
                if sig is not None:
                    candidates.append(sig)
                self._report_progress("executing", idx, total)
        finally:
            # 手动取消未启动的 future（cancel_futures 是 Python 3.9+，本项目跑 3.7）
            for f in futures:
                if not f.done():
                    f.cancel()
            executor.shutdown(wait=False)

        if errors > 0:
            err_pct = errors / total * 100 if total else 0
            level = logger.warning if err_pct > 5 else logger.info
            level(f"[{self.name}] 评估错误 {errors}/{total} ({err_pct:.1f}%), last={last_err}")

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
        except Exception as e:
            logger.debug(f"实时行情兜底 {code}: {e}")
            return {"涨跌幅": 0.0, "最新价": default_price, "换手率": 0.0}

    def _build_result(self, candidates: List[StockSignal], trade_date: str,
                      scanned: int, sort_key=None) -> ScreenResult:
        """统一构造筛选结果：signals 为 Top N 展示用，all_signals 为全量命中供合并用

        优化：
        1. 命中数上限控制（单策略不超过300只）
        2. 排名百分位评分（解决满分扎堆、增加区分度）
        """
        from ..core.constants import MAX_HITS_PER_STRATEGY
        MAX_HITS = MAX_HITS_PER_STRATEGY
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
        # 结果级日期对齐到命中信号里最新的那根 bar，标签与数据一致
        result_date = trade_date
        bar_dates = [s.trade_date for s in candidates if s.trade_date]
        if bar_dates:
            result_date = max(bar_dates)
        return ScreenResult(
            strategy_name=self.name,
            strategy_desc=self.description,
            signals=candidates[:self.top_n],
            trade_date=result_date,
            total_scanned=scanned,
            all_signals=candidates[:],
        )


def last_vol_ratio(vol_ratio, i: int = -1, default: float = 1.0) -> float:
    """安全读取量比序列在位置 i 的值，缺失时兜底 default。

    统一各策略里 5 种写法不一的「取量比、兜底 1.0」逻辑：vol_ratio 可能为 None
    （指标缺失），该位置可能为 NaN（窗口不足），下标也可能越界。此前部分策略只
    判 NaN 不判 None，指标缺失时会 AttributeError。
    """
    if vol_ratio is None:
        return default
    try:
        v = vol_ratio.iloc[i]
    except (IndexError, KeyError):
        return default
    if pd.isna(v):
        return default
    return float(v)


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
