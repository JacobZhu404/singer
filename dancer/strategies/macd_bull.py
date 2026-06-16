from dancer.strategies.base import BaseStrategy
from dancer.models.signal import StockSignal
import pandas as pd
import talib


class MACDBullStrategy(BaseStrategy):
    """MACD趋势策略"""

    name = "macd_bull"
    description = "MACD金叉且多头排列"
    weight = 0.3

    def evaluate(self, code: str, df: pd.DataFrame) -> StockSignal | None:
        if len(df) < 30:
            return None

        close = df['close'].values
        macd, signal, hist = talib.MACD(close, fastperiod=12, slowperiod=26, signalperiod=9)

        # 判断条件
        if macd[-1] > signal[-1] and macd[-2] <= signal[-2]:  # 金叉
            score = 60 + min(40, (macd[-1] - signal[-1]) * 100)
            return StockSignal(
                code=code,
                name=code,
                score=score,
                factors=[],
                signals=["MACD金叉"],
                reason="MACD形成金叉，看多"
            )
        return None