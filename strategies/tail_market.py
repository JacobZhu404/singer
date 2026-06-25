"""
尾盘强势策略（Tail Market Strength，日线近似版）

来源：A 股散户圈广为流传的「14:30 后尾盘选股法」。方法论本身合理
（本质 = 相对强度 + 温和放量 + 趋势确认 + 尾盘买点），但常被包装成
"一键 APP"导流话术，需警惕——本策略只复刻**可验证的量化规则**，
不依赖任何第三方 APP。

原始规则与本实现的对应：
  规则                          本实现
  ────────────────────────────────────────────────────────
  14:30 后操作                  日线盘后扫描（近似口径，非分时）
  涨幅 3%-5%                    pct_chg ∈ [2.5%, 6%]（略宽容）
  量比 > 1                      vol_ratio > 1.0（自算 5 日基准）
  换手率 5%-10%                 turnover ∈ [3%, 12%]（略宽容）
  市值 50亿-200亿               ⚠️ 跳过：本项目暂未抓总股本/总市值
  成交量稳定持续放量            近 5 日量能稳定增长（无单日异常）
  均线多头排列                  MA5 > MA10 > MA20 > MA60
  分时强于大盘                  ⚠️ 跳过：需要分钟线，本项目仅日线
  尾盘创新高且不破均线          收盘价创 20 日新高 且 close > MA5

注意：因缺市值与分时数据，本策略相比原版更宽口径、需结合其他策略
联合过滤；不建议单独作为买入依据。
"""

import pandas as pd
import numpy as np
from typing import Optional
import logging

from .base import BaseStrategy, StockSignal, _compute_risk_flags, last_vol_ratio

logger = logging.getLogger(__name__)


class TailMarketStrategy(BaseStrategy):
    """尾盘强势（日线近似版）"""
    name = "tail_market"
    description = "尾盘选股法日线近似：温和上涨+量能配合+均线多头+收盘创新高"
    base_win_rate = 0.54

    # 规则参数（略宽于原版以适应日线粒度）
    PCT_MIN = 2.5
    PCT_MAX = 6.0
    TURN_MIN = 3.0
    TURN_MAX = 12.0
    VR_MIN = 1.0
    BREAKOUT_DAYS = 20

    def __init__(self, top_n: int = 10):
        super().__init__(top_n=top_n)

    def _evaluate_single_stock(self, code, scanner, name_map, trade_date) -> Optional[StockSignal]:
        indicators = scanner.get_indicators(code, days=120)
        if not indicators or len(indicators["kline"]) < 25:
            raise self._SkipStock()

        df = indicators["kline"]
        close = df["close"].astype(float)
        high = df["high"].astype(float)
        vol = df["vol"].astype(float) if "vol" in df.columns else pd.Series(0.0, index=df.index)
        if "volume" in df.columns:
            vol = vol.fillna(df["volume"].astype(float))

        i = len(df) - 1
        price_now = float(close.iloc[i])
        if price_now < 0.1 or price_now > 1000:
            raise self._SkipStock()

        # ── 实时口径优先：涨幅/换手率走 realtime；缺则降级到 K线计算 ──
        quote = self._get_quote(scanner, code, price_now)
        pct = quote.get("涨跌幅")
        if pct is None or pct == 0:
            prev = float(close.iloc[i - 1]) if i >= 1 else price_now
            pct = (price_now - prev) / prev * 100 if prev > 0 else 0
        pct = float(pct)
        turnover = float(quote.get("换手率", 0) or 0)

        # ── 涨幅区间过滤（温和强势）──
        if not (self.PCT_MIN <= pct <= self.PCT_MAX):
            return None

        # ── 换手率过滤（活跃但不过热）──
        # 若 turnover 缺失（=0），不做强过滤，标记为 unknown
        turnover_ok = (self.TURN_MIN <= turnover <= self.TURN_MAX) if turnover > 0 else None
        if turnover_ok is False:
            return None

        # ── 量比 > 1 ──
        vr = last_vol_ratio(indicators.get("vol_ratio"), i)
        if vr < self.VR_MIN:
            return None

        signals = []
        score = 0

        signals.append(f"温和涨幅{pct:.1f}%")
        score += 15
        if turnover_ok:
            signals.append(f"换手{turnover:.1f}%")
            score += 15
        elif turnover_ok is None:
            signals.append("换手未知")
            score += 5

        if vr >= 1.5:
            signals.append(f"放量{vr:.1f}倍")
            score += 18
        else:
            signals.append(f"温和量能{vr:.1f}倍")
            score += 10

        # ── 量能稳定性：近 5 日无单日突兀放量/缩量 ──
        if i >= 5:
            recent_vol = vol.iloc[i - 4:i + 1].astype(float)
            base = recent_vol.mean()
            if base > 0:
                deviations = (recent_vol - base).abs() / base
                if deviations.max() < 1.5:  # 单日偏离均值 < 150%
                    signals.append("量能稳定")
                    score += 10

        # ── 均线多头：MA5 > MA10 > MA20 > MA60 ──
        ma_dict = indicators.get("ma", {})
        ma5 = ma_dict.get("ma5")
        ma10 = ma_dict.get("ma10")
        ma20 = ma_dict.get("ma20")
        ma60 = ma_dict.get("ma60")

        def _last(s):
            if s is None or len(s) == 0 or pd.isna(s.iloc[-1]):
                return None
            return float(s.iloc[-1])

        m5, m10, m20, m60 = _last(ma5), _last(ma10), _last(ma20), _last(ma60)
        full_bull = all(v is not None for v in (m5, m10, m20, m60)) and (m5 > m10 > m20 > m60)
        partial_bull = all(v is not None for v in (m5, m10, m20)) and (m5 > m10 > m20)
        if full_bull:
            signals.append("均线四线多头")
            score += 20
        elif partial_bull:
            signals.append("均线三线多头")
            score += 10
        else:
            # 趋势不成立直接淘汰
            return None

        # ── 尾盘买点：收盘创 N 日新高 且 不破 MA5 ──
        if i + 1 >= self.BREAKOUT_DAYS + 1:
            recent_high = float(high.iloc[i - self.BREAKOUT_DAYS:i].max())
            if price_now >= recent_high and (m5 is None or price_now > m5):
                signals.append(f"收盘创{self.BREAKOUT_DAYS}日新高")
                score += 22
            elif price_now >= recent_high * 0.98:
                signals.append("贴近阶段高点")
                score += 8
            else:
                return None
        else:
            return None

        if score < 70:
            return None

        return StockSignal(
            ts_code=code,
            name=name_map.get(code, code),
            strategy=self.name,
            score=min(int(score), 100),
            win_rate=None,
            signals=signals,
            latest_price=round(float(quote.get("最新价", price_now)), 2),
            pct_chg=round(pct, 2),
            volume_ratio=round(vr, 2),
            risk_flags=_compute_risk_flags(df),
            trade_date=trade_date,
            extra={
                "turnover": round(turnover, 2) if turnover > 0 else None,
                "ma_bull": "full" if full_bull else "partial",
            },
        )
