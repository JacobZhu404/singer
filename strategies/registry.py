"""
策略注册器 - 统一管理所有策略

排序规则（2026-06-25 重排）：按语义簇分组，组内按权重/默认选中优先级排列。
分组依据：策略的核心信号语义（顺势 / 反转 / 突破 / 形态），与 docs/backtest_report.md
里的"短打/长持/均衡"画像维度正交——同一组内仍可包含不同持有期画像。

分组：
  G1 趋势顺势 (6): macd_bull, right_side, strong_stock, golden_cross, tail_market, momentum
  G2 反转超跌 (4): td_sequential, rsi_oversold, bollinger_lower_bounce, reversal
  G3 突破·创新高 (5): volume_breakout, bollinger_breakout, rps_breakout, high_tight_flag, limit_up_gene
  G4 形态独立 (1): chanlun_strict （Phase B Jaccard <8% vs 其他全部，是策略池最独立的一支）
"""

from .macd_bull import MACDBullStrategy
from .strong_stock import StrongStockStrategy
from .td_sequential import TDSequentialStrategy
from .right_side import RightSideTradingStrategy
from .limit_up_gene import LimitUpGeneStrategy
from .rsi_oversold import RSIOversoldStrategy
from .bollinger_lower_bounce import BollingerLowerBounceStrategy
from .bollinger_breakout import BollingerBreakoutStrategy
from .volume_breakout import VolumeBreakoutStrategy
from .chanlun_strict import ChanlunStrictStrategy
from .golden_cross import GoldenCrossStrategy
from .momentum import MomentumStrategy
from .rps_breakout import RpsBreakoutStrategy
from .high_tight_flag import HighTightFlagStrategy
from .tail_market import TailMarketStrategy
from .reversal import ReversalStrategy

# 策略注册表（按分组排序）
STRATEGY_REGISTRY = {
    # ─────────────────────────────────────────────────────────────
    # G1 · 趋势顺势：均线多头 / MACD 健康 / 顺势确认
    # ─────────────────────────────────────────────────────────────
    "macd_bull": {
        "cls": MACDBullStrategy,
        "name": "MACD多头排列",
        "description": "DIF/DEA同时在零轴以上，MACD金叉，均线多头排列",
        "tags": ["趋势", "中线"],
        "icon": "📈",
        "weight": 1.2,
        "group": "趋势顺势",
    },
    "right_side": {
        "cls": RightSideTradingStrategy,
        "name": "右侧交易",
        "description": "突破关键阻力位后介入，均线金叉，顺势而为",
        "tags": ["突破", "中线"],
        "icon": "⚡",
        "weight": 1.1,
        "group": "趋势顺势",
    },
    "strong_stock": {
        "cls": StrongStockStrategy,
        "name": "强势股选股",
        "description": "放量+红肥绿瘦+小阳/缺口(互斥)+MACD零轴(v2优化)",
        "tags": ["强势", "短线"],
        "icon": "💪",
        "weight": 1.3,
        "group": "趋势顺势",
    },
    "golden_cross": {
        "cls": GoldenCrossStrategy,
        "name": "均线金叉(宽松)",
        "description": "macd_bull宽松版：3线多头+RSI，比macd_bull更早入场",
        "tags": ["金叉", "趋势", "均线", "宽松"],
        "icon": "✨",
        "weight": 0.9,
        "group": "趋势顺势",
    },
    "tail_market": {
        "cls": TailMarketStrategy,
        "name": "尾盘强势(日线近似)",
        "description": "温和涨幅+量能配合+均线多头+收盘创新高（缺市值/分时数据）",
        "tags": ["尾盘", "强势", "短线", "近似"],
        "icon": "🌅",
        "weight": 0.9,
        "group": "趋势顺势",
    },
    "momentum": {
        "cls": MomentumStrategy,
        "name": "动量策略",
        "description": "价格动量排名前10%+量能确认，捕捉趋势延续",
        "tags": ["动量", "趋势", "强势"],
        "icon": "🚀",
        "weight": 1.1,
        "group": "趋势顺势",
    },

    # ─────────────────────────────────────────────────────────────
    # G2 · 反转超跌：触底 / 超卖 / 均值回归
    # ─────────────────────────────────────────────────────────────
    "td_sequential": {
        "cls": TDSequentialStrategy,
        "name": "神奇九转",
        "description": "TD Sequential买入九转，反转信号，适合短线抄底",
        "tags": ["反转", "短线"],
        "icon": "🔮",
        "weight": 1.0,
        "group": "反转超跌",
    },
    "rsi_oversold": {
        "cls": RSIOversoldStrategy,
        "name": "RSI 超卖",
        "description": "RSI<30超卖区域，价格反弹概率高，适合震荡市抄底",
        "tags": ["超卖", "反弹", "短线"],
        "icon": "📉",
        "weight": 0.9,
        "group": "反转超跌",
    },
    "bollinger_lower_bounce": {
        "cls": BollingerLowerBounceStrategy,
        "name": "布林下轨反弹",
        "description": "价格触及布林带下轨或附近，配合缩量止跌，均值回归反弹",
        "tags": ["布林带", "反弹", "均值回归"],
        "icon": "📊",
        "weight": 0.9,
        "group": "反转超跌",
    },
    "reversal": {
        "cls": ReversalStrategy,
        "name": "横截面反转",
        "description": "全市场按近5日跌幅横截面排名+当日企稳，捕捉超跌反弹（A股短期反转效应）",
        "tags": ["反转", "超跌", "横截面", "短线"],
        "icon": "🔄",
        "weight": 1.0,
        "group": "反转超跌",
    },

    # ─────────────────────────────────────────────────────────────
    # G3 · 突破·创新高：量价突破 / RPS / 旗形 / 涨停基因
    # ─────────────────────────────────────────────────────────────
    "volume_breakout": {
        "cls": VolumeBreakoutStrategy,
        "name": "量价突破",
        "description": "量比>2倍 + 价格突破近期高点，视为有效突破信号",
        "tags": ["突破", "放量", "短线"],
        "icon": "🚀",
        "weight": 1.0,
        "group": "突破创新高",
    },
    "bollinger_breakout": {
        "cls": BollingerBreakoutStrategy,
        "name": "布林收口突破",
        "description": "布林带 10 日最窄 + 放量突破中轨 / 逼近上轨，捕捉波动率扩张起步",
        "tags": ["布林带", "收口", "突破", "波动率"],
        "icon": "📊",
        "weight": 0.9,
        "group": "突破创新高",
    },
    "rps_breakout": {
        "cls": RpsBreakoutStrategy,
        "name": "RPS相对强度突破",
        "description": "欧奈尔RPS：多周期加权强度+创阶段新高+放量，捕捉领涨股",
        "tags": ["欧奈尔", "相对强度", "突破", "领涨"],
        "icon": "🏆",
        "weight": 1.1,
        "group": "突破创新高",
    },
    "high_tight_flag": {
        "cls": HighTightFlagStrategy,
        "name": "高紧旗形",
        "description": "欧奈尔高紧旗形：旗杆暴涨+高位窄幅缩量整理，蓄势待突破",
        "tags": ["欧奈尔", "旗形", "强庄", "突破"],
        "icon": "🚩",
        "weight": 1.0,
        "group": "突破创新高",
    },
    "limit_up_gene": {
        "cls": LimitUpGeneStrategy,
        "name": "涨停基因",
        "description": "近期真封板(close==high) + 回撤 5-18% 甜区 + 量价拐头 (v2，去循环引用)",
        "tags": ["涨停", "回踩", "短线"],
        "icon": "🔥",
        "weight": 1.0,
        "group": "突破创新高",
    },

    # ─────────────────────────────────────────────────────────────
    # G4 · 形态独立：缠论（与全部其他策略 Jaccard < 8%，是策略池最独立的一支）
    # ─────────────────────────────────────────────────────────────
    "chanlun_strict": {
        "cls": ChanlunStrictStrategy,
        "name": "缠论严格版",
        "description": "包含处理→分型(5K)→笔→中枢→背驰→三类买点（推荐）",
        "tags": ["缠论", "严格", "中枢", "背驰"],
        "icon": "📐",
        "weight": 1.1,
        "group": "形态独立",
    },
}


def get_strategy(name: str, top_n: int = 10):
    """根据策略名称创建策略实例"""
    if name not in STRATEGY_REGISTRY:
        raise ValueError(f"未知策略: {name}，可选: {list(STRATEGY_REGISTRY.keys())}")
    meta = STRATEGY_REGISTRY[name]
    return meta["cls"](top_n=top_n)


def list_strategies():
    """列出所有可用策略（含分组字段）"""
    return [
        {
            "id": k,
            "name": v["name"],
            "description": v["description"],
            "tags": v["tags"],
            "icon": v["icon"],
            "group": v.get("group", "其他"),
        }
        for k, v in STRATEGY_REGISTRY.items()
    ]
