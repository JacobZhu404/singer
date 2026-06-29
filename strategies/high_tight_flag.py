"""
高紧旗形策略（High Tight Flag，欧奈尔 William O'Neil）

欧奈尔形态学里最强、也最稀有的形态之一：
  1. 旗杆：短期内（约 4-8 周）股价近乎翻倍（暴涨 80%+）
  2. 旗面：随后 1-3 周高位窄幅整理，回调浅（一般 < 25%），波动收窄、缩量
  3. 突破：放量突破旗面上沿，进入新一轮主升

A股日线近似实现：
  - 旗杆：过去约 25-45 交易日内某段累计涨幅 >= 80%
  - 旗面：最近约 5-15 交易日为整理区，最大回撤 <= 25%，振幅收窄、量能萎缩
  - 当前价仍贴近区间高点（蓄势待突破）或刚放量突破
适用：强庄股二波启动前的潜伏 / 突破

风险：该形态稀有，且常伴随高波动，务必结合风险标签与仓位管理。
"""

import pandas as pd
import numpy as np
from typing import Optional
import logging

from .base import BaseStrategy, StockSignal, _compute_risk_flags, last_vol_ratio

logger = logging.getLogger(__name__)


class HighTightFlagStrategy(BaseStrategy):
    """高紧旗形（欧奈尔）"""
    name = "high_tight_flag"
    description = "欧奈尔高紧旗形：旗杆暴涨+高位窄幅缩量整理，蓄势待突破"
    base_win_rate = 0.47  # 30日实测胜率，2026-06-29 全量回测（仅18笔，噪声大）

    # 参数
    # 注：严格欧奈尔口径 POLE_MIN_GAIN=80% 在 52 周回测里只命中 3 笔（HTF 本就
    # 是最稀有形态），样本太小无法统计验证。放宽到 50%（仍是 ≤45 日内的强势
    # 翻倍前奏），换取可统计的样本量；突破质量门槛（收盘放量突破）保持不变。
    POLE_MIN_GAIN = 50.0      # 旗杆最小涨幅(%)
    POLE_MAX_DAYS = 45        # 旗杆考察最大交易日数
    FLAG_MIN_DAYS = 5         # 旗面最少天数
    FLAG_MAX_DAYS = 20        # 旗面最多天数
    FLAG_MAX_DRAWDOWN = 25.0  # 旗面最大回撤(%)

    def __init__(self, top_n: int = 10):
        super().__init__(top_n=top_n)

    def _evaluate_single_stock(self, code, scanner, name_map, trade_date) -> Optional[StockSignal]:
        indicators = scanner.get_indicators(code, days=120)
        if not indicators or len(indicators["kline"]) < 30:
            raise self._SkipStock()

        df = indicators["kline"]
        close = df["close"].astype(float)
        high = df["high"].astype(float)
        low = df["low"].astype(float)
        vol = df["vol"].astype(float) if "vol" in df.columns else pd.Series(0.0, index=df.index)
        if "volume" in df.columns:
            vol = vol.fillna(df["volume"].astype(float))
        n = len(close)
        i = n - 1

        price_now = float(close.iloc[i])
        if price_now < 0.1 or price_now > 1000:
            raise self._SkipStock()

        # ── 1. 识别旗面（最近 FLAG_MIN..MAX 天的高位整理区）──
        # 旗面起点 fs：尝试不同旗面长度，取「回撤最浅且高位」的窗口。
        # 注意：旗面 = 突破日**之前**的整理区，统计窗口为 [fs..i-1]，不含今日。
        # 否则今日盘中插针会被算进 flag_high，close > flag_high 永远拿不到。
        best_flag = None  # (flag_len, flag_high, flag_low, drawdown, fs)
        for flag_len in range(self.FLAG_MIN_DAYS, self.FLAG_MAX_DAYS + 1):
            fs = i - flag_len
            if fs <= 0:
                break
            seg_high = float(high.iloc[fs:i].max())
            seg_low = float(low.iloc[fs:i].min())
            if seg_high <= 0:
                continue
            drawdown = (seg_high - seg_low) / seg_high * 100
            if drawdown <= self.FLAG_MAX_DRAWDOWN:
                # 优先取更长且仍满足浅回撤的旗面
                best_flag = (flag_len, seg_high, seg_low, drawdown, fs)
        if best_flag is None:
            return None
        flag_len, flag_high, flag_low, flag_dd, fs = best_flag

        # ── 2. 识别旗杆（旗面之前的暴涨段）──
        pole_end = fs - 1
        if pole_end <= 0:
            return None
        pole_start_lo = max(0, pole_end - self.POLE_MAX_DAYS)
        # 旗杆底：旗面前 POLE_MAX_DAYS 内的最低收盘
        pole_low = float(close.iloc[pole_start_lo:pole_end + 1].min())
        pole_top = float(close.iloc[pole_end])  # 旗杆顶≈旗面起点前一日收盘
        # 用旗面最高也参与旗杆涨幅评估（取较大者更贴近"翻倍"）
        pole_peak = max(pole_top, flag_high)
        if pole_low <= 0:
            return None
        pole_gain = (pole_peak - pole_low) / pole_low * 100
        if pole_gain < self.POLE_MIN_GAIN:
            return None

        # 真"旗面"必须在旗杆顶**附近或之上**整理。若 flag_high < pole_top*0.98，
        # 说明旗面在下滑（已开始破位），不是高位窄幅整理 — 这是 16 周回测里
        # 30 日 α=-9.14% 的主因之一（catches stocks already topping out）。
        if flag_high < pole_top * 0.98:
            return None

        signals = []
        score = 0

        # 旗杆涨幅评分（涨幅越大越接近经典 HTF，给分越高）
        if pole_gain >= 120:
            score += 40
            signals.append(f"旗杆暴涨{pole_gain:.0f}%")
        elif pole_gain >= 100:
            score += 35
            signals.append(f"旗杆翻倍{pole_gain:.0f}%")
        elif pole_gain >= 80:
            score += 30
            signals.append(f"旗杆强涨{pole_gain:.0f}%")
        else:
            score += 24
            signals.append(f"旗杆上涨{pole_gain:.0f}%")

        # 旗面紧致度评分（回撤越浅越好）
        if flag_dd <= 12:
            score += 30
            signals.append(f"旗面极紧(回撤{flag_dd:.0f}%)")
        elif flag_dd <= 18:
            score += 22
            signals.append(f"旗面紧致(回撤{flag_dd:.0f}%)")
        else:
            score += 12
            signals.append(f"旗面整理(回撤{flag_dd:.0f}%)")

        # ── 3. 旗面缩量（整理期均量 < 旗杆期均量）──
        pole_vol = float(vol.iloc[pole_start_lo:pole_end + 1].mean())
        flag_vol = float(vol.iloc[fs:i].mean())
        if pole_vol > 0 and flag_vol > 0:
            vol_shrink = flag_vol / pole_vol
            if vol_shrink < 0.6:
                score += 15
                signals.append(f"旗面缩量({vol_shrink:.0%})")
            elif vol_shrink < 0.85:
                score += 8
                signals.append(f"旗面量能收敛({vol_shrink:.0%})")

        # ── 4. 当前位置：贴近旗面高点（蓄势）或放量突破 ──
        vr = last_vol_ratio(indicators.get("vol_ratio"), i)

        # 只接受**收盘价**有效突破：旧版用 `high >= flag_high`（盘中插针），
        # 又开了 `near_high` 后门（5% 内即可），结果选到一堆"刚做完旗杆开始
        # 跌出旗面"的票。改成 close 突破 + 放量。
        breakout = float(close.iloc[i]) > flag_high and vr >= 1.3
        if not breakout:
            return None
        score += 25
        signals.append(f"放量突破旗面({vr:.1f}倍)")

        # 阈值随旗杆门槛同步下调 85→75：放宽后弱旗杆(24分)+松旗面(12分)+突破(25)=61
        # 仍被挡；要么强旗杆要么紧旗面，再叠加突破才入选。
        if score < 75:
            return None

        quote = self._get_quote(scanner, code, price_now)
        pct = quote.get("涨跌幅", 0.0) or 0.0
        return StockSignal(
            ts_code=code,
            name=name_map.get(code, code),
            strategy=self.name,
            score=min(int(score), 100),
            win_rate=None,
            signals=signals,
            latest_price=round(float(quote.get("最新价", price_now)), 2),
            pct_chg=round(float(pct), 2),
            volume_ratio=round(vr, 2),
            risk_flags=_compute_risk_flags(df),
            trade_date=trade_date,
            extra={
                "pole_gain": round(pole_gain, 1),
                "flag_len": flag_len,
                "flag_drawdown": round(flag_dd, 1),
                "flag_high": round(flag_high, 2),
            },
        )
