from __future__ import annotations
from dancer.models.signal import StockSignal
from dancer.strategies.registry import StrategyRegistry
import pandas as pd
from typing import Optional


class Scorer:
    """评分引擎"""

    def __init__(self):
        self.strategies = StrategyRegistry.list_all()

    def score(self, code: str, df: pd.DataFrame, weights: Optional[dict] = None) -> list[StockSignal]:
        """评分"""
        results = []

        for name, strategy in self.strategies.items():
            weight = weights.get(name, strategy.weight) if weights else strategy.weight
            signal = strategy.evaluate(code, df)
            if signal:
                signal.score *= weight
                results.append(signal)

        # 合并多策略结果
        if not results:
            return []

        # 按分数排序
        results.sort(key=lambda x: x.score, reverse=True)
        return results[:10]

    def merge_signals(self, signals: list[StockSignal]) -> list[StockSignal]:
        """合并信号"""
        if not signals:
            return []

        # 按code合并，分数累加
        merged = {}
        for s in signals:
            if s.code in merged:
                merged[s.code].score += s.score
                merged[s.code].signals.extend(s.signals)
            else:
                merged[s.code] = s

        result = list(merged.values())
        result.sort(key=lambda x: x.score, reverse=True)
        return result