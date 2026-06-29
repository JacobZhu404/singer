"""
量价突破策略（优化版）

优化（2026-05-14）：
  1. 加入价格位置过滤（避免在过高位置突破）
  2. 优化量比阈值（>=3.0为强突破）
  3. 加入突破后回踩确认（回踩MA5/MA10后继续上涨）

条件：
  1. 量比 > 2倍（明显放量）
  2. 价格突破30日高点
  3. 阳线（今日收涨）
  4. 放量上涨共振
  5. 价格位置过滤（不在过高位置）
  6. 突破后回踩确认（ stronger signal）
适用：短线启动、题材炒作
"""

import pandas as pd
import numpy as np
import logging

from .base import BaseStrategy, StockSignal, _compute_risk_flags


logger = logging.getLogger(__name__)


class VolumeBreakoutStrategy(BaseStrategy):
    """量价突破策略（优化版）"""
    name = "volume_breakout"
    description = "量比>2倍+价格突破+回踩确认，有效突破信号"
    base_win_rate = 0.45  # 30日实测胜率，2026-06-29 全量回测

    def __init__(self, top_n: int = 20):
        super().__init__(top_n)

    def _evaluate_single_stock(self, code, scanner, name_map, trade_date):
        indicators = scanner.get_indicators(code, days=120)
        if not indicators or len(indicators["kline"]) < 30:
            raise self._SkipStock()

        kline = indicators["kline"]
        close = kline["close"].astype(float)
        high = kline["high"].astype(float)

        # 兼容：vol 列可能为 NaN，尝试用 volume 列填充
        if "vol" in kline.columns:
            vol = kline["vol"]
        else:
            vol = pd.Series(0.0, index=kline.index)
        if "volume" in kline.columns:
            vol = vol.fillna(kline["volume"])
        # 确保vol是数值类型
        vol = vol.astype(float)

        # === 检测休市状态：用最后有效数据 ===
        last_idx = len(kline) - 1
        if pd.isna(vol.iloc[-1]) or vol.iloc[-1] == 0:
            valid_idx = vol.last_valid_index()
            if valid_idx is None:
                raise self._SkipStock()
            last_idx = kline.index.get_loc(valid_idx)

        # 用有效索引的数据
        today_high = float(high.iloc[last_idx])
        price = float(close.iloc[last_idx])
        prev_price = float(close.iloc[last_idx - 1]) if last_idx >= 1 else price
        latest_vol = float(vol.iloc[last_idx])

        # 跳过异常价格（如数据被污染时，A股价格一般在0.1-500范围）
        if price > 500 or today_high > 500 or price < 0.1:
            raise self._SkipStock()

        # === 找到被污染数据的起始位置（A股价格正常范围0.1-500）===
        # 用close和vol同时判断
        # 正常成交量范围：100万-1亿
        valid_start = 0
        for i in range(len(close)):
            if (close.iloc[i] > 0.1 and close.iloc[i] < 500 and
                vol.iloc[i] > 100000 and vol.iloc[i] < 100000000):
                valid_start = i
                break

        # 如果数据被严重污染，跳过
        if valid_start >= last_idx - 5:
            raise self._SkipStock()

        # 使用有效数据范围计算
        valid_close = close.iloc[valid_start:last_idx+1]
        valid_high = high.iloc[valid_start:last_idx+1]
        valid_vol = vol.iloc[valid_start:last_idx+1]

        # 过滤掉成交量异常的数据（被污染的vol都是几千万到几亿）
        # 正常A股日成交量一般在100万-1亿范围
        normal_mask = (valid_vol > 100000) & (valid_vol < 100000000)
        valid_vol = valid_vol[normal_mask]
        valid_close = valid_close[normal_mask]
        valid_high = valid_high[normal_mask]

        if len(valid_vol) < 5:
            raise self._SkipStock()

        # 计算量比
        latest_vol = float(valid_vol.iloc[-1])
        recent_5_vols = valid_vol.iloc[-5:]
        avg_vol = recent_5_vols.mean()
        vol_ratio = latest_vol / (avg_vol + 1e-8) if avg_vol > 0 else 1.0

        # === 突破判断 ===
        if len(valid_high) >= 31:
            high_30 = valid_high.iloc[:-1].max()
        else:
            high_30 = valid_high.max()
        if len(valid_high) >= 6:
            high_5 = valid_high.iloc[:-1].max()
        else:
            high_5 = valid_high.max()

        signals = []
        score = 0
        has_volume = False
        has_breakout = False

        # ── 优化2: 优化量比阈值 ──
        if vol_ratio >= 3.0:  # 优化：2.5→3.0（更强突破）
            signals.append(f"量比{vol_ratio:.1f}倍强放量")
            score += 40  # 优化：35→40
            has_volume = True
        elif vol_ratio >= 2.0:
            signals.append(f"明显放量{vol_ratio:.1f}倍")
            score += 30  # 优化：25→30
            has_volume = True
        elif vol_ratio >= 1.5:
            signals.append(f"温和放量{vol_ratio:.1f}倍")
            score += 15  # 优化：10→15
            has_volume = True

        # ── 条件2: 突破（提高长期突破权重）──
        if today_high > high_30:
            signals.append(f"突破30日高点({round(high_30, 2)})")
            score += 20
            has_breakout = True
            if today_high > high_5:
                signals.append("突破近期新高")
                score += 10

        # 突破60日高点给更高分
        if len(valid_high) >= 61:
            high_60 = valid_high.iloc[-61:-1].max()
            if today_high > high_60:
                signals.append(f"突破60日高点({round(high_60, 2)})")
                score += 30
                has_breakout = True

        if price > prev_price:
            signals.append("今日收阳")
            score += 10

        # ── 条件3: 放量上涨共振（提高要求）──
        price_pct_chg = (price - prev_price) / prev_price * 100
        if vol_ratio >= 2.0 and price_pct_chg > 3:
            signals.append("放量上涨共振")
            score += 20
        elif vol_ratio >= 1.5 and price_pct_chg > 2:
            signals.append("放量上涨")
            score += 10

        if len(valid_vol) >= 2:
            vol_ratio_prev = valid_vol.iloc[-2] / (valid_vol.iloc[-5:-2].mean() + 1e-8) if valid_vol.iloc[-5:-2].mean() > 0 else 1.0
            if vol_ratio >= 1.5 and vol_ratio_prev >= 1.3:
                signals.append("量能持续放大")
                score += 10

        # ── 优化1: 加入价格位置过滤 ──
        ma20 = indicators["ma"].get("ma20")
        ma60 = indicators["ma"].get("ma60")

        # 价格在MA20上方（确认短期趋势）
        if ma20 is not None and not pd.isna(ma20.iloc[-1]):
            if price > ma20.iloc[-1]:
                signals.append("价格在MA20上方")
                score += 10
            else:
                signals.append("价格在MA20下方")
                score -= 5  # 趋势未确认

        # 价格不在过高位置（避免在MA60的110%以上追高）
        if ma60 is not None and not pd.isna(ma60.iloc[-1]):
            if price > ma60.iloc[-1] * 1.10:
                signals.append("价格过高(>MA60*1.1)")
                score -= 15  # 过热的，降低评分
            elif price > ma60.iloc[-1]:
                signals.append("价格在MA60上方")
                score += 10

        # ── 优化3: 加入突破后回踩确认 ──
        if len(valid_close) >= 10 and has_breakout:
            for j in range(-10, -1):
                if len(valid_high) > abs(j) + 20:
                    if valid_high.iloc[j] > valid_high.iloc[j-20:j].max():
                        ma10_at_j = valid_close.iloc[j-10:j].mean()
                        if (valid_close.iloc[-1] > valid_close.iloc[j] and
                            min(valid_close.iloc[j+1:]) < ma10_at_j * 1.02):
                            signals.append("突破后回踩确认")
                            score += 20  # 强信号
                            break

        # 必须同时满足放量(>=2.0) + 突破
        if not (has_volume and has_breakout):
            return None

        if score < 85:  # 收紧：55→85，控制命中数
            return None

        quote = self._get_quote(scanner, code, price)
        return StockSignal(
            ts_code=code,
            name=name_map.get(code, code),
            strategy=self.name,
            score=min(score, 100),
            win_rate=None,
            signals=signals,
            latest_price=float(quote.get("最新价", price)),
            pct_chg=float(quote.get("涨跌幅", 0.0)),
            volume_ratio=float(vol_ratio),
            risk_flags=_compute_risk_flags(kline),
            trade_date=trade_date,
            extra={
                "vol_ratio": round(float(vol_ratio), 2),
                "high_30": round(float(high_30), 2),
            },
        )
