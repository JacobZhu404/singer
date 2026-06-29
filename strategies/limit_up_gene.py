"""涨停基因（重写版本）

设计目标：捕捉**有过封板背书 → 回踩企稳 → 重新量价拐头**的票。

为什么重写：旧版有三个结构性问题（见 strategy_audit_findings + 8周回测）：
  1. 候选池 = 当日封板列表（`get_limit_list`）→ 选股标的本身不可成交，
     回测口径下又被 #19 的封板过滤几乎全部 drop。
  2. "涨停次数" 用 `单日涨幅 ≥ 9.5%` 判定（不要求 close==high），
     把振幅大的票误计为封板。
  3. "再次启动" 只是个静态回撤过滤器，不带量价拐头确认 → 8周回测显示
     2日 α≈-2.2%（追涨惩罚）。

新版的"真涨停基因"：
  - 候选池：**全市场**（去掉 _get_codes 覆盖）。
  - 形态：近 lookback 日内出现过**真封板**（close==high & 触板），
    随后**已回撤** [5%, 18%]（甜区 [8%, 15%]），
    当日**不能封板**（必须可成交），但呈现**量价拐头**（阳线 + 量比 ≥ 1）。
  - 排除追高：当日不能既大幅放量又大涨。
  - 排除崩盘：回撤超 18% 视为基本面恶化，不再是回踩。

依据：A 股短期反转效应（mom5 IC=-0.027）告诉我们"追当日涨停"=负 α；
但"封板基因+回踩企稳"是另一类形态——板块龙头/资金标的回吐后再启动，
不与短期反转因子打架。该形态在新版回测里会被 #19 的封板进场过滤"保护"
（当日封板会被 drop，不会污染样本）。
"""

import logging
from typing import List, Optional

import pandas as pd

from .base import BaseStrategy, StockSignal, _compute_risk_flags
from ..utils.indicators import get_limit_pct

logger = logging.getLogger(__name__)


class LimitUpGeneStrategy(BaseStrategy):
    name = "limit_up_gene"
    description = "涨停基因 - 近期真封板+回踩企稳+量价拐头（v2）"
    base_win_rate = 0.45  # 30日实测胜率，2026-06-29 全量回测

    # 形态参数（可被回测网格替换）
    lookback = 15            # 回看几天找封板
    pullback_min = 5.0       # 最小回撤（小于则没回踩，仍是追高）
    pullback_max = 18.0      # 最大回撤（大于则已破位，不是回踩）
    pullback_sweet_lo = 8.0  # 回撤甜区
    pullback_sweet_hi = 15.0
    score_threshold = 45

    def _is_real_limit_up(self, df: pd.DataFrame, idx: int, limit_pct: float) -> bool:
        """真封板：当日涨幅触板 **且** close == high（即收在最高=封死）。

        单纯比较涨幅会把"盘中触板尾盘回落"误计为涨停，去掉这种放量震荡。
        """
        if idx <= 0 or idx >= len(df):
            return False
        try:
            close = float(df.iloc[idx]["close"])
            high = float(df.iloc[idx]["high"])
            prev_close = float(df.iloc[idx - 1]["close"])
        except Exception:
            return False
        if prev_close <= 0 or high <= 0:
            return False
        pct = (close - prev_close) / prev_close * 100.0
        # 容忍 0.3% 浮点/价位最小变动；high 与 close 允许 0.5% 误差（弱封板也算）
        return (pct >= limit_pct - 0.3) and (abs(close - high) / high <= 0.005)

    def _evaluate_single_stock(self, code, scanner, name_map, trade_date) -> Optional[StockSignal]:
        indicators = scanner.get_indicators(code, days=60)
        if not indicators or len(indicators["kline"]) < 30:
            raise self._SkipStock()

        df = indicators["kline"]
        if len(df) < self.lookback + 3:
            raise self._SkipStock()

        close = df["close"].astype(float)
        open_ = df["open"].astype(float) if "open" in df.columns else close
        high = df["high"].astype(float) if "high" in df.columns else close
        i = len(df) - 1

        limit_pct = get_limit_pct(code, name_map.get(code))

        # ── 第 0 关：当日不能封板（封板买不到）──
        if self._is_real_limit_up(df, i, limit_pct):
            raise self._SkipStock()
        # 当日涨幅离封板太近（>limit-1）也跳过——大概率盘中触板，T+1 高开难入场
        prev_close = float(close.iloc[i - 1])
        if prev_close <= 0:
            raise self._SkipStock()
        pct_today = (float(close.iloc[i]) - prev_close) / prev_close * 100.0
        if pct_today >= limit_pct - 1.0:
            raise self._SkipStock()

        # ── 第 1 关：lookback 内找到至少一次真封板 ──
        lb_start = max(1, i - self.lookback)
        limit_idxs: List[int] = []
        for j in range(lb_start, i):  # 不含当日
            if self._is_real_limit_up(df, j, limit_pct):
                limit_idxs.append(j)
        if not limit_idxs:
            return None

        last_limit_idx = limit_idxs[-1]
        days_since_limit = i - last_limit_idx
        # 距上次封板太近（< 2 日）通常还在连板节奏，留给 momentum；
        # 太远（> lookback）已在 lookback 之外
        if days_since_limit < 2:
            return None

        # ── 第 2 关：从封板后高点回撤幅度 ──
        # 用「封板那根 high」到「之后的最高 high」中的最大者作为参考顶
        after_limit = df.iloc[last_limit_idx: i + 1]
        peak = float(after_limit["high"].max())
        cur = float(close.iloc[i])
        if peak <= 0:
            return None
        pullback = (peak - cur) / peak * 100.0
        if pullback < self.pullback_min or pullback > self.pullback_max:
            return None

        # ── 第 3 关：量价拐头（阳线 + 量比 ≥ 1）──
        vol_ratio = indicators.get("vol_ratio")
        vr = 1.0
        if vol_ratio is not None and not pd.isna(vol_ratio.iloc[i]):
            vr = float(vol_ratio.iloc[i])
        open_today = float(open_.iloc[i])
        is_red = cur > open_today
        if not is_red or vr < 1.0:
            return None
        # 排除追高：放巨量又大涨 → 已变追涨标的
        if vr > 3.0 and pct_today > 5.0:
            return None

        # ── 评分 ──
        signals: List[str] = []
        score = 0.0
        n_limits = len(limit_idxs)

        if n_limits >= 3:
            score += 35
            signals.append(f"近{self.lookback}日真封板{n_limits}次")
        elif n_limits == 2:
            score += 25
            signals.append(f"近{self.lookback}日真封板2次")
        else:
            score += 15
            signals.append(f"近{self.lookback}日真封板1次")

        if days_since_limit <= 5:
            score += 10
            signals.append(f"刚回踩{days_since_limit}日")
        elif days_since_limit <= 10:
            score += 5

        if self.pullback_sweet_lo <= pullback <= self.pullback_sweet_hi:
            score += 20
            signals.append(f"回撤甜区({pullback:.1f}%)")
        else:
            score += 10
            signals.append(f"回撤{pullback:.1f}%")

        score += 10
        signals.append(f"当日阳线(+{pct_today:.1f}%)")

        if 1.2 <= vr < 2.0:
            score += 10
            signals.append(f"温和放量(量比{vr:.1f}x)")
        elif 2.0 <= vr <= 3.0:
            score += 5
            signals.append(f"明显放量(量比{vr:.1f}x)")

        # MACD DIF 翻红或在零轴附近
        try:
            dif, _, _ = indicators["macd"]
            d = float(dif.iloc[i]) if not pd.isna(dif.iloc[i]) else None
            if d is not None and -0.05 <= d <= 0.20:
                score += 10
                signals.append("MACD近零轴上沿")
        except Exception:
            pass

        if score < self.score_threshold:
            return None

        quote = self._get_quote(scanner, code, cur)
        latest = float(quote.get("最新价", cur) or cur)
        pct_real = quote.get("涨跌幅", None)
        pct_real = float(pct_real) if pct_real is not None else round(pct_today, 2)

        return StockSignal(
            ts_code=code,
            name=name_map.get(code, code),
            strategy=self.name,
            score=min(round(score, 1), 100.0),
            win_rate=None,
            signals=signals,
            latest_price=round(latest, 2),
            pct_chg=round(pct_real, 2),
            volume_ratio=round(vr, 2),
            risk_flags=_compute_risk_flags(df),
            trade_date=trade_date,
            extra={
                "n_real_limits": n_limits,
                "days_since_limit": days_since_limit,
                "pullback_pct": round(pullback, 2),
            },
        )
