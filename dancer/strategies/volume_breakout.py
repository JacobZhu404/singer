from dancer.strategies.base import BaseStrategy
from dancer.models.signal import StockSignal
import pandas as pd


class VolumeBreakoutStrategy(BaseStrategy):
    """成交量突破策略"""

    name = "volume_breakout"
    description = "成交量突破20日均值2倍"
    weight = 0.3

    def evaluate(self, code: str, df: pd.DataFrame) -> StockSignal | None:
        if len(df) < 20:
            return None

        vol = df['volume'].values
        ma5 = vol[-5:].mean()
        ma20 = vol[-20:].mean()

        if vol[-1] > ma20 * 2 and ma5 > ma20:  # 放量突破
            score = 60 + min(40, (vol[-1] / ma20 - 2) * 20)
            return StockSignal(
                code=code,
                name=code,
                score=score,
                factors=[],
                signals=["成交量突破"],
                reason=f"成交量{int(vol[-1])}突破20日均值{int(ma20)}"
            )
        return None