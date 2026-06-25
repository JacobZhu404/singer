"""
横截面反转策略（cross-sectional reversal）

依据 factor-ic-findings：A 股短周期的稳健边际是**反转**而非动量
（近期跌得多的股票，次日/数日内反弹概率更高）。区别于已有的「神奇九转 /
RSI 超卖」单股择时——本策略在**全市场横截面**上排名，挑「近 N 日跌幅居前」者。

实现上是「薄策略」：横截面排名由 utils/cross_section.compute_reversal_scores
一次性算好挂在 scanner 上（screen() 里触发），_evaluate_single_stock 只做查表 +
安全护栏。护栏的目的：避免「接飞刀」与「捕捉不可成交的收益」——
  1. 排除当日跌停（跌停买不进；factor-ic-findings 警示反转收益会被涨跌停高估）
  2. 要求当日企稳（收盘回升 / 阳线），不接连续下杀
  3. 排除近 N 日暴跌（>25%，疑似利空/退市风险）
  4. 价格与流动性下限（剔除仙股/僵尸股）

注意：含成本（T+1 + 印花税 + 滑点）回测前，本策略信号仅供横向比较参考。
"""

import pandas as pd
from typing import Optional
import logging

from .base import BaseStrategy, StockSignal, _compute_risk_flags
from ..utils.indicators import get_limit_pct
from ..utils.cross_section import compute_reversal_scores, _to6

logger = logging.getLogger(__name__)


class ReversalStrategy(BaseStrategy):
    """横截面反转（近 N 日跌幅居前 + 企稳确认）"""
    name = "reversal"
    description = "全市场横截面反转：近5日跌幅居前+当日企稳，捕捉超跌反弹（依据A股短期反转效应）"
    base_win_rate = 0.53

    lookback = 5          # 反转窗口（交易日）
    min_rev_score = 70.0  # 横截面反转分阈值（取跌幅居前的约 30%）
    max_drawdown = 25.0   # 近 N 日跌幅超过此值视为利空暴跌，排除

    def __init__(self, top_n: int = 10):
        super().__init__(top_n=top_n)

    def screen(self, stock_list, scanner=None, max_workers: int = 20, stop_event=None):
        """先在全市场算横截面反转分，再走基类逐只评估。

        覆盖 screen() 而非依赖引擎预计算，使本策略在 Web 一键推荐与 CLI 单跑
        （run_single 不调 _precalc）两条路径下都能自洽。
        """
        from ..data.fetcher import market_scanner
        sc = scanner if scanner is not None else market_scanner
        sc.load()
        codes = self._get_codes(stock_list)
        compute_reversal_scores(sc, codes, lookback=self.lookback)
        return super().screen(stock_list, scanner=scanner, max_workers=max_workers, stop_event=stop_event)

    def prepare_for_date(self, scanner, codes, trade_date: str) -> None:
        """回测路径：BacktestEngine 切换日期后调用，把当日截面挂到 scanner 上。

        PIT scanner（_pit_df 取 as_of 及之前数据）→ 走自定义 df_getter；
        其他 scanner → 退回默认（_kline_cache）。
        """
        pit_df = getattr(scanner, "_pit_df", None)
        if callable(pit_df):
            # PIT 需要按 6 位代码取 as_of 之前的 K 线
            def _getter(sc, code6):
                return pit_df(code6, days=max(self.lookback + 30, 60))
            compute_reversal_scores(scanner, codes, lookback=self.lookback, df_getter=_getter)
        else:
            compute_reversal_scores(scanner, codes, lookback=self.lookback)

    def _evaluate_single_stock(self, code, scanner, name_map, trade_date) -> Optional[StockSignal]:
        code6 = _to6(code)

        # ST/退市股已被 name_map 剔除——不在映射内直接跳过
        if code not in name_map and code6 not in name_map:
            raise self._SkipStock()

        scores = getattr(scanner, "_reversal_scores", None) or {}
        rec = scores.get(code6)
        if rec is None:
            raise self._SkipStock()

        ret5 = rec["ret"]
        rev_score = rec["score"]

        # 只关注超跌候选（近端为负收益）
        if ret5 >= 0:
            return None
        # 横截面靠前者才入选
        if rev_score < self.min_rev_score:
            return None
        # 近 N 日暴跌（疑似利空/退市），排除接飞刀
        if ret5 < -self.max_drawdown:
            return None

        indicators = scanner.get_indicators(code, days=60)
        if not indicators or len(indicators["kline"]) < 20:
            raise self._SkipStock()
        df = indicators["kline"]
        close = df["close"].astype(float)
        price_now = float(close.iloc[-1])
        if price_now < 1.0 or price_now > 1000:  # 剔除仙股/异常价
            raise self._SkipStock()
        prev_close = float(close.iloc[-2])
        if prev_close <= 0:
            raise self._SkipStock()

        # 当日涨跌幅用 K 线推导（比实时行情更可靠，离线/回测也可用）
        pct_today = (price_now - prev_close) / prev_close * 100.0

        # ── 护栏1：排除当日跌停/近跌停（买不进 + 反转收益会被高估）──
        limit = get_limit_pct(code, name_map.get(code))
        if pct_today <= -(limit - 0.5):
            raise self._SkipStock()

        # ── 护栏2：要求当日企稳（收盘回升 或 收阳）──
        open_today = float(df["open"].iloc[-1]) if "open" in df.columns else price_now
        rebounded = price_now > prev_close
        red_candle = price_now > open_today
        if not (rebounded or red_candle):
            return None

        signals = [
            f"近{self.lookback}日跌幅居前({ret5:.1f}%)",
            f"横截面反转分{rev_score:.0f}",
        ]
        score = rev_score * 0.7
        if rebounded:
            signals.append(f"止跌回升(+{pct_today:.1f}%)")
            score += 10
        elif red_candle:
            signals.append("当日收阳企稳")
            score += 5

        vol_ratio = indicators.get("vol_ratio")
        vr = 1.0
        if vol_ratio is not None and not pd.isna(vol_ratio.iloc[-1]):
            vr = float(vol_ratio.iloc[-1])
        # 企稳放量加成（缩量企稳可信度低）
        if vr >= 1.2:
            score += 5
            signals.append(f"企稳放量({vr:.1f}倍)")

        quote = self._get_quote(scanner, code, price_now)
        pct = quote.get("涨跌幅", None)
        pct = float(pct) if pct is not None else round(pct_today, 2)

        return StockSignal(
            ts_code=code,
            name=name_map.get(code, code),
            strategy=self.name,
            score=min(round(score, 1), 100.0),
            win_rate=None,
            signals=signals,
            latest_price=round(float(quote.get("最新价", price_now) or price_now), 2),
            pct_chg=round(pct, 2),
            volume_ratio=round(vr, 2),
            risk_flags=_compute_risk_flags(df),
            trade_date=trade_date,
            extra={
                "ret5": ret5,
                "reversal_score": rev_score,
                "rank_pct": rec["rank_pct"],
            },
        )
