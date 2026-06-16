import talib
import pandas as pd
import numpy as np
from typing import Optional


class FactorCalculator:
    """因子计算器"""

    @staticmethod
    def to_df(klines: list[dict]) -> pd.DataFrame:
        """转换为DataFrame"""
        df = pd.DataFrame(klines)
        # 统一列名
        cols = {'日期': 'date', '开盘': 'open', '最高': 'high', '最低': 'low', '收盘': 'close', '成交量': 'volume'}
        df = df.rename(columns=cols)
        df = df.sort_values('date')
        return df

    @staticmethod
    def macd(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> dict:
        """MACD指标"""
        close = df['close'].values
        macd, signal_line, hist = talib.MACD(close, fastperiod=fast, slowperiod=slow, signalperiod=signal)
        return {
            'macd': float(macd[-1]) if not np.isnan(macd[-1]) else None,
            'signal': float(signal_line[-1]) if not np.isnan(signal_line[-1]) else None,
            'hist': float(hist[-1]) if not np.isnan(hist[-1]) else None,
        }

    @staticmethod
    def rsi(df: pd.DataFrame, period: int = 14) -> dict:
        """RSI指标"""
        close = df['close'].values
        rsi = talib.RSI(close, timeperiod=period)
        return {'rsi': float(rsi[-1]) if not np.isnan(rsi[-1]) else None}

    @staticmethod
    def boll(df: pd.DataFrame, period: int = 20, std: float = 2.0) -> dict:
        """布林带"""
        close = df['close'].values
        upper, middle, lower = talib.BBANDS(close, timeperiod=period, nbdevup=std, nbdevdn=std)
        return {
            'upper': float(upper[-1]) if not np.isnan(upper[-1]) else None,
            'middle': float(middle[-1]) if not np.isnan(middle[-1]) else None,
            'lower': float(lower[-1]) if not np.isnan(lower[-1]) else None,
        }

    @staticmethod
    def volume_ma(df: pd.DataFrame, period: int = 5) -> dict:
        """成交量均线"""
        vol = df['volume'].values
        ma = talib.SMA(vol, timeperiod=period)
        return {'volume_ma': float(ma[-1]) if not np.isnan(ma[-1]) else None}

    @staticmethod
    def calculate_all(df: pd.DataFrame) -> dict:
        """计算所有因子"""
        return {
            **FactorCalculator.macd(df),
            **FactorCalculator.rsi(df),
            **FactorCalculator.boll(df),
            **FactorCalculator.volume_ma(df),
        }