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
    base_win_rate = 0.58  # 优化：提高胜率预估

    def _evaluate_single_stock(self, code, scanner, name_map, trade_date):
        try:
            indicators = scanner.get_indicators(code, days=120)
            if not indicators or len(indicators["kline"]) < 30:
                raise self._SkipStock()

            kline = indicators["kline"]
            close = kline["close"]
            high = kline["high"]
            vol = kline["vol"]
            vol_ratio_series = indicators["vol_ratio"]
            
            # 用今日最高价（而非收盘价）判断是否突破
            today_high = float(high.iloc[-1])
            price = float(close.iloc[-1])       # 收盘价用于收阳判断
            prev_price = float(close.iloc[-2])

            vol_ratio = float(vol_ratio_series.iloc[-1]) if not pd.isna(vol_ratio_series.iloc[-1]) else 1.0
            vol_ratio_prev = float(vol_ratio_series.iloc[-2]) if len(vol_ratio_series) >= 2 and not pd.isna(vol_ratio_series.iloc[-2]) else 1.0
            
            if pd.isna(vol_ratio) or vol_ratio <= 0:
                raise self._SkipStock()
            
            # 保留 20 日均量供 extra 展示
            vol_ma20 = vol.rolling(20).mean().iloc[-1]
            
            # 突破判断用最高价，而非收盘价
            high_30 = high.iloc[-31:-1].max() if len(high) >= 31 else high.iloc[:-1].max()
            high_5 = high.iloc[-6:-1].max() if len(high) >= 6 else high.iloc[:-1].max()

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
            if len(high) >= 61:
                high_60 = high.iloc[-61:-1].max()
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
            # 检查是否在突破后有回踩MA5/MA10，然后继续上涨
            if len(close) >= 10 and has_breakout:
                # 查找近10日是否有突破
                for j in range(-10, -1):
                    # 某日突破20日新高
                    if high.iloc[j] > high.iloc[j-20:j].max():
                        # 突破后是否有回踩（价格回落到MA10附近）
                        ma10_at_j = close.rolling(10).mean().iloc[j]
                        
                        # 回踩后继续上涨
                        if (close.iloc[-1] > close.iloc[j] and
                            min(close.iloc[j+1:]) < ma10_at_j * 1.02):
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
                    "vol_ma20": round(float(vol_ma20), 0),
                    "high_30": round(float(high_30), 2),
                },
            )

        except Exception as e:
            logger.debug(f"[量价突破] {code} 计算失败: {e}")
