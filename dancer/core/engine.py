from dancer.data.fetcher import DataFetcher
from dancer.strategies.registry import StrategyRegistry
from dancer.models.signal import ScreenResult, StockSignal
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class Engine:
    """选股引擎"""

    def __init__(self):
        self.fetcher = DataFetcher()
        StrategyRegistry.load_default()

    def screen(self, codes: list[str] = None, limit: int = 20) -> ScreenResult:
        """执行筛选"""
        # 获取股票列表
        if codes is None:
            stock_list = self.fetcher.get_stock_list()
            codes = [s['code'] for s in stock_list[:500]]  # 限制数量

        all_signals = []
        for code in codes:
            try:
                data = self.fetcher.get_stock_data(code)
                if not data or not data.klines:
                    continue

                from dancer.factors.talib import FactorCalculator
                df = FactorCalculator.to_df([k.model_dump() for k in data.klines])

                # 运行所有策略
                signals = self._evaluate_code(code, df)
                all_signals.extend(signals)
            except Exception as e:
                logger.warning(f"处理{code}失败: {e}")

        # 合并结果
        merged = self._merge_signals(all_signals)

        return ScreenResult(
            stocks=merged[:limit],
            total=len(merged),
            timestamp=datetime.now().isoformat()
        )

    def _evaluate_code(self, code: str, df):
        """评估单只股票"""
        signals = []
        name = None
        for name, strategy in StrategyRegistry.list_all().items():
            try:
                result = strategy.evaluate(code, df)
                if result:
                    signals.append(result)
            except Exception as e:
                logger.debug(f"{code} {name}策略失败: {e}")
        return signals

    def _merge_signals(self, signals: list[StockSignal]) -> list[StockSignal]:
        """合并信号"""
        if not signals:
            return []

        # 按股票分组
        stock_signals: dict[str, list[StockSignal]] = {}
        for s in signals:
            if s.code not in stock_signals:
                stock_signals[s.code] = []
            stock_signals[s.code].append(s)

        # 合并每个股票的信号
        merged = []
        for code, sigs in stock_signals.items():
            if len(sigs) == 1:
                merged.append(sigs[0])
            else:
                # 合并多个信号
                total_score = sum(s.score for s in sigs)
                avg_score = total_score / len(sigs)
                all_factors = []
                all_signal_names = []
                for s in sigs:
                    all_factors.extend(s.factors)
                    all_signal_names.extend(s.signals)

                merged.append(StockSignal(
                    code=code,
                    name=sigs[0].name,
                    score=min(100, avg_score),
                    factors=all_factors[:10],
                    signals=list(set(all_signal_names)),
                    reason=f"符合{len(sigs)}个策略条件"
                ))

        # 按评分排序
        merged.sort(key=lambda x: x.score, reverse=True)
        return merged