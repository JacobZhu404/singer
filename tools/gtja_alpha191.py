"""GTJA Alpha191 因子库（Top IR 子集）

来源：国泰君安 2017 年研报《基于短周期价量特征的多因子选股体系》。
本模块只实现 IC/IR 文献里报告 Top 9 + 几个代表性因子（共 12 个），
按 panel-wise 实现（行=日期，列=股票），便于后续做 IC 和组合回测。

口径：
  - 本地 cache 没有 AMOUNT，VWAP 用典型价代理 TYP = (H+L+C)/3；
  - 算子约定遵守原研报（DELTA / DELAY / CORR / RANK / TSRANK 等）；
  - RANK 是**横截面**（按行 axis=1 排名），TSRANK 是**时序**（个股自身窗口内的分位）；
  - 因子方向保留原公式（即原文 IR 为负的因子不在此处取反；调用方按 IR 符号解读）。
"""

from __future__ import annotations
from typing import Dict
import numpy as np
import pandas as pd


# ───────────────────────── 算子（panel-wise） ─────────────────────────

def delay(x: pd.DataFrame, n: int) -> pd.DataFrame:
    return x.shift(n)


def delta(x: pd.DataFrame, n: int) -> pd.DataFrame:
    return x - x.shift(n)


def mean(x: pd.DataFrame, n: int) -> pd.DataFrame:
    return x.rolling(n, min_periods=max(2, n // 2)).mean()


def std(x: pd.DataFrame, n: int) -> pd.DataFrame:
    return x.rolling(n, min_periods=max(2, n // 2)).std()


def sum_(x: pd.DataFrame, n: int) -> pd.DataFrame:
    return x.rolling(n, min_periods=max(2, n // 2)).sum()


def tsmax(x: pd.DataFrame, n: int) -> pd.DataFrame:
    return x.rolling(n, min_periods=max(2, n // 2)).max()


def log_(x: pd.DataFrame) -> pd.DataFrame:
    return np.log(x.where(x > 0))


def rank_cs(x: pd.DataFrame) -> pd.DataFrame:
    """横截面排名（每日全市场，pct=True 归一到 [0,1]）。"""
    return x.rank(axis=1, pct=True)


def tsrank(x: pd.DataFrame, n: int) -> pd.DataFrame:
    """时序排名（pandas 2.2+ 提供 rolling.rank，纯 C 实现，比 apply 快 100x）。"""
    return x.rolling(n, min_periods=max(2, n // 2)).rank(pct=True)


def corr_(x: pd.DataFrame, y: pd.DataFrame, n: int) -> pd.DataFrame:
    """逐列滚动 Pearson 相关（每只股票自身两条序列）。"""
    return x.rolling(n, min_periods=max(2, n // 2)).corr(y)


def typ_price(p: dict) -> pd.DataFrame:
    """典型价代理 VWAP（无 AMOUNT 时的标准做法）。"""
    return (p["high"] + p["low"] + p["close"]) / 3.0


# ───────────────────────── 因子（Top 9 + 3 代表）─────────────────────────

def alpha83(p: dict) -> pd.DataFrame:
    """Alpha83  IR=0.74（多头）
    -1 * CORR(RANK(MEAN(VOL,20)), RANK(DELTA(CLOSE,5)), 10)
    放量回调 → 短期反转上涨。
    """
    v_rank = rank_cs(mean(p["vol"], 20))
    c_rank = rank_cs(delta(p["close"], 5))
    return -corr_(v_rank, c_rank, 10)


def alpha99(p: dict) -> pd.DataFrame:
    """Alpha99  IR=0.73（多头）
    RANK(SUM(CORR(RANK(VOL), RANK((C-VWAP)/VWAP), 5), 8))
    日内收盘持续强于均价 + 同步放量 → 多头资金进场。
    """
    vwap = typ_price(p)
    devi = (p["close"] - vwap) / vwap
    inner = corr_(rank_cs(p["vol"]), rank_cs(devi), 5)
    return rank_cs(sum_(inner, 8))


def alpha62(p: dict) -> pd.DataFrame:
    """Alpha62  IR=0.66（多头）
    -1 * CORR(RANK(DELTA(VOL,3)), RANK(STD(CLOSE,8)), 6)
    缩量但价格震荡 → 筹码交换充分，后续走强。
    """
    return -corr_(rank_cs(delta(p["vol"], 3)), rank_cs(std(p["close"], 8)), 6)


def alpha90(p: dict) -> pd.DataFrame:
    """Alpha90  IR=0.66（多头）
    SUM(RANK(DELTA(LOG(VOL),2)), 5)
    连续 5 日成交量环比放大 → 资金持续流入。
    """
    return sum_(rank_cs(delta(log_(p["vol"]), 2)), 5)


def alpha32(p: dict) -> pd.DataFrame:
    """Alpha32  IR=0.60（多头）
    ((C-L) - (H-C)) / (H-L)
    日内 K 线多空强度，[-1, 1]。
    """
    rng = (p["high"] - p["low"]).replace(0, np.nan)
    return ((p["close"] - p["low"]) - (p["high"] - p["close"])) / rng


def alpha16(p: dict) -> pd.DataFrame:
    """Alpha16  IR=0.56（多头）
    TSRANK((C-VWAP)/VWAP, 20)
    今日收盘相对均价偏离在自身 20 日中的分位。
    """
    vwap = typ_price(p)
    return tsrank((p["close"] - vwap) / vwap, 20)


def alpha176(p: dict) -> pd.DataFrame:
    """Alpha176  IR=-0.55（空头，因子值越高未来收益越差）
    -1 * TSRANK( SUM(DELTA(C,1),15) / MEAN(VOL,10), 12 )
    无量大涨（短期累计涨幅大但成交量低迷）→ 短期回调。
    注意：原公式有 -1，本实现按原公式保留 -1；IC 为正表示反向因子有效。
    """
    ratio = sum_(delta(p["close"], 1), 15) / mean(p["vol"], 10).replace(0, np.nan)
    return -tsrank(ratio, 12)


def alpha74(p: dict) -> pd.DataFrame:
    """Alpha74  IR=-0.52（空头）
    STD(DELTA(C,1),12) / MEAN(VOL,20)
    小量支撑大波动 → 见顶。
    """
    return std(delta(p["close"], 1), 12) / mean(p["vol"], 20).replace(0, np.nan)


def alpha70(p: dict) -> pd.DataFrame:
    """Alpha70  IR=-0.48（空头）
    MAX(DELTA(H,3),8) / VOL
    冲高放量不足 → 衰竭。
    """
    return tsmax(delta(p["high"], 3), 8) / p["vol"].replace(0, np.nan)


# 三个代表性"对照因子"（来自不同语义簇，用来测真实相关性）

def alpha1(p: dict) -> pd.DataFrame:
    """Alpha1（量价反转入门标杆，多头）
    -1 * CORR(RANK(DELTA(LOG(VOL),1)), RANK((C-O)/O), 6)
    """
    return -corr_(rank_cs(delta(log_(p["vol"]), 1)),
                  rank_cs((p["close"] - p["open"]) / p["open"]), 6)


def alpha2(p: dict) -> pd.DataFrame:
    """Alpha2（日内多空力量差分，多头）
    -1 * DELTA( ((C-L)-(H-C))/(H-L), 1 )
    """
    rng = (p["high"] - p["low"]).replace(0, np.nan)
    intra = ((p["close"] - p["low"]) - (p["high"] - p["close"])) / rng
    return -delta(intra, 1)


def alpha120(p: dict) -> pd.DataFrame:
    """Alpha120（VWAP 修复，多头）
    RANK((VWAP - C)/C)
    """
    vwap = typ_price(p)
    return rank_cs((vwap - p["close"]) / p["close"])


# ───────────────────────── 注册表 ─────────────────────────

FACTORS = {
    # Top 6 多头（原文 IR>0）
    "alpha83":  (alpha83,  "+", 0.74, "量价背离"),
    "alpha99":  (alpha99,  "+", 0.73, "日内量价同步"),
    "alpha62":  (alpha62,  "+", 0.66, "量价波动背离"),
    "alpha90":  (alpha90,  "+", 0.66, "成交量动量"),
    "alpha32":  (alpha32,  "+", 0.60, "日内 K 线结构"),
    "alpha16":  (alpha16,  "+", 0.56, "日内价格时序极值"),
    # Top 3 空头（原文 IR<0）
    "alpha176": (alpha176, "-", -0.55, "量价顶背离"),
    "alpha74":  (alpha74,  "-", -0.52, "波动-量能失衡"),
    "alpha70":  (alpha70,  "-", -0.48, "极值衰竭"),
    # 对照（不同语义簇，用于相关性矩阵）
    "alpha1":   (alpha1,   "+", None, "量价反转(对照)"),
    "alpha2":   (alpha2,   "+", None, "日内动力(对照)"),
    "alpha120": (alpha120, "+", None, "VWAP 修复(对照)"),
}


def compute_all(panels: dict) -> Dict[str, pd.DataFrame]:
    """跑全部因子，返回 {name: factor_panel}。"""
    out = {}
    for name, (fn, _sign, _ir, _cat) in FACTORS.items():
        out[name] = fn(panels)
    return out
