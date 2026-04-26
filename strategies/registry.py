"""
策略注册器 - 统一管理所有策略
"""

from .macd_bull import MACDBullStrategy
from .strong_stock import StrongStockStrategy
from .td_sequential import TDSequentialStrategy
from .right_side import RightSideTradingStrategy
from .limit_up_gene import LimitUpGeneStrategy
from .rsi_oversold import RSIOversoldStrategy
from .bollinger_bands import BollingerBandsStrategy
from .volume_breakout import VolumeBreakoutStrategy
from .chanlun import ChanlunStrategy
from .golden_cross import GoldenCrossStrategy

# 策略注册表
STRATEGY_REGISTRY = {
    "macd_bull": {
        "cls": MACDBullStrategy,
        "name": "MACD多头排列",
        "description": "DIF/DEA同时在零轴以上，MACD金叉，均线多头排列",
        "tags": ["趋势", "中线"],
        "icon": "📈",
    },
    "strong_stock": {
        "cls": StrongStockStrategy,
        "name": "强势股选股",
        "description": "放量上涨、红肥绿瘦、五连小阳、跳空缺口综合评分",
        "tags": ["强势", "短线"],
        "icon": "💪",
    },
    "td_sequential": {
        "cls": TDSequentialStrategy,
        "name": "神奇九转",
        "description": "TD Sequential买入九转，反转信号，适合短线抄底",
        "tags": ["反转", "短线"],
        "icon": "🔮",
    },
    "right_side": {
        "cls": RightSideTradingStrategy,
        "name": "右侧交易",
        "description": "突破关键阻力位后介入，均线金叉，顺势而为",
        "tags": ["突破", "中线"],
        "icon": "⚡",
    },
    "limit_up_gene": {
        "cls": LimitUpGeneStrategy,
        "name": "涨停基因",
        "description": "近期有涨停记录，涨停后未大幅回落，题材活跃",
        "tags": ["涨停", "短线"],
        "icon": "🔥",
    },
    "rsi_oversold": {
        "cls": RSIOversoldStrategy,
        "name": "RSI 超卖",
        "description": "RSI<30超卖区域，价格反弹概率高，适合震荡市抄底",
        "tags": ["超卖", "反弹", "短线"],
        "icon": "📉",
    },
    "bollinger_bands": {
        "cls": BollingerBandsStrategy,
        "name": "布林带反弹",
        "description": "价格触及布林带下轨或附近，配合缩量，暗示反弹概率高",
        "tags": ["布林带", "反弹", "均值回归"],
        "icon": "📊",
    },
    "volume_breakout": {
        "cls": VolumeBreakoutStrategy,
        "name": "量价突破",
        "description": "量比>2倍 + 价格突破近期高点，视为有效突破信号",
        "tags": ["突破", "放量", "短线"],
        "icon": "🚀",
    },
    "chanlun": {
        "cls": ChanlunStrategy,
        "name": "缠论中枢",
        "description": "缠中说禅：底分型 + MACD背驰 + 中枢支撑 + 三类买点",
        "tags": ["缠论", "中枢", "背驰"],
        "icon": "🔱",
    },
    "golden_cross": {
        "cls": GoldenCrossStrategy,
        "name": "均线金叉",
        "description": "MA5上穿MA10金叉 + 多头排列 + RSI确认，适合趋势启动初期",
        "tags": ["金叉", "趋势", "均线"],
        "icon": "✨",
    },
}


def get_strategy(name: str, top_n: int = 10):
    """根据策略名称创建策略实例"""
    if name not in STRATEGY_REGISTRY:
        raise ValueError(f"未知策略: {name}，可选: {list(STRATEGY_REGISTRY.keys())}")
    meta = STRATEGY_REGISTRY[name]
    return meta["cls"](top_n=top_n)


def list_strategies():
    """列出所有可用策略"""
    return [
        {
            "id": k,
            "name": v["name"],
            "description": v["description"],
            "tags": v["tags"],
            "icon": v["icon"],
        }
        for k, v in STRATEGY_REGISTRY.items()
    ]
