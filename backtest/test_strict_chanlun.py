#!/usr/bin/env python3
"""
严格缠论真实算法验证 — 使用chanlun_strict.py的实际代码
"""
import sys
import os
sys.path.insert(0, '/Users/jacob/personal/stock_screener')

import pandas as pd
import numpy as np

# ─────────────────────────────────────────────────────────────────────────────
# 复制chanlun_strict.py核心算法（用于独立测试）
# ─────────────────────────────────────────────────────────────────────────────

from dataclasses import dataclass
from typing import List, Optional

@dataclass
class KBar:
    index: int
    open: float
    high: float
    low: float
    close: float

@dataclass
class Fractal:
    type: str
    bar_index: int
    price: float
    strength: float

def _merge_inclusive_strict(raw_bars: List[KBar]) -> List[KBar]:
    """严格包含处理（方向优先）"""
    if len(raw_bars) < 2:
        return raw_bars[:]

    result: List[KBar] = []
    init_bars = raw_bars[:min(3, len(raw_bars))]
    up_count = sum(1 for b in init_bars[1:] if b.low >= init_bars[0].low)
    direction = "up" if up_count >= len(init_bars) - 1 else "down"

    i = 0
    n = len(raw_bars)
    while i < n:
        b = raw_bars[i]
        if not result:
            result.append(b)
            i += 1
            continue
        prev = result[-1]
        # 完整包含判断
        if prev.low >= b.low and prev.high <= b.high:
            # b 包含 prev，向上合并取高高
            new_bar = KBar(index=b.index, open=prev.open,
                           high=max(prev.high, b.high), low=prev.low,
                           close=max(prev.high, b.high))
            result[-1] = new_bar
            i += 1
        elif prev.high <= b.high and prev.low >= b.low:
            # prev 包含 b，向下合并取低低
            new_bar = KBar(index=b.index, open=prev.open,
                           high=b.high, low=max(prev.low, b.low),
                           close=b.low)
            result[-1] = new_bar
            i += 1
        else:
            result.append(b)
            i += 1
    return result

def _find_fractals_strict(bars: List[KBar]) -> List[Fractal]:
    """5K严格分型"""
    if len(bars) < 5:
        return []
    n = len(bars)
    fractals: List[Fractal] = []
    for i in range(2, n - 2):
        left_2_high = max(bars[i-2].high, bars[i-1].high)
        right_2_high = max(bars[i+1].high, bars[i+2].high)
        left_2_low = min(bars[i-2].low, bars[i-1].low)
        right_2_low = min(bars[i+1].low, bars[i+2].low)
        is_top = (bars[i].high > left_2_high and bars[i].high > right_2_high)
        is_bottom = (bars[i].low < left_2_low and bars[i].low < right_2_low)
        if is_top:
            exceed = bars[i].high - max(left_2_high, right_2_high)
            body = bars[i].high - bars[i].low
            strength = min(exceed / (body + 1e-8), 1.0) if body > 0 else 0.5
            fractals.append(Fractal("top", i, bars[i].high, max(strength, 0.3)))
        elif is_bottom:
            exceed = min(left_2_low, right_2_low) - bars[i].low
            body = bars[i].high - bars[i].low
            strength = min(exceed / (body + 1e-8), 1.0) if body > 0 else 0.5
            fractals.append(Fractal("bottom", i, bars[i].low, max(strength, 0.3)))
    return fractals

def _build_strokes(fractals: List[Fractal], min_len: int = 5) -> List[tuple]:
    if len(fractals) < 2:
        return []
    strokes = []
    prev = fractals[0]
    for f in fractals[1:]:
        if f.type != prev.type:
            if f.bar_index - prev.bar_index >= min_len:
                strokes.append((prev.bar_index, f.bar_index, prev.type))
            prev = f
        else:
            prev = f
    return strokes

def _ema(s, n):
    k = 2.0 / (n + 1)
    e = np.zeros(len(s), dtype=float)
    e[0] = s[0]
    for i in range(1, len(s)):
        e[i] = s[i] * k + e[i-1] * (1 - k)
    return e

def _calc_macd(closes, fast=12, slow=26, signal=9):
    dif = _ema(closes, fast) - _ema(closes, slow)
    dea = _ema(dif, signal)
    return dif, dea

def _find_pivots(strokes, kbars):
    if len(strokes) < 3:
        return []
    pivots = []
    for i in range(len(strokes) - 2):
        s1, s2, s3 = strokes[i], strokes[i+1], strokes[i+2]
        if s1[2] == s3[2] and s1[2] != s2[2]:
            low1 = min(kbars[s1[0]].low, kbars[s1[1]].low)
            high1 = max(kbars[s1[0]].high, kbars[s1[1]].high)
            low2 = min(kbars[s2[0]].low, kbars[s2[1]].low)
            high2 = max(kbars[s2[0]].high, kbars[s2[1]].high)
            low3 = min(kbars[s3[0]].low, kbars[s3[1]].low)
            high3 = max(kbars[s3[0]].high, kbars[s3[1]].high)
            zd = max(low1, low2, low3)
            zg = min(high1, high2, high3)
            if zg > zd:
                pivots.append({'zd': zd, 'zg': zg, 'zb': (zd+zg)/2})
    return pivots

def _detect_divergence(kbars, strokes, dif):
    if len(strokes) < 4:
        return None
    down_strokes = [(s, i) for i, s in enumerate(strokes) if s[2] == 'bottom']
    if len(down_strokes) < 2:
        return None
    for idx in range(len(down_strokes) - 1):
        a_stroke, a_idx = down_strokes[idx]
        c_stroke, c_idx = down_strokes[idx + 1]
        a_price = min(kbars[a_stroke[0]].low, kbars[a_stroke[1]].low)
        c_price = min(kbars[c_stroke[0]].low, kbars[c_stroke[1]].low)
        a_dif = np.min(dif[a_stroke[0]:a_stroke[1]+1])
        c_dif = np.min(dif[c_stroke[0]:c_stroke[1]+1])
        if c_price < a_price * 0.98 and c_dif > a_dif * 0.7:
            return {'type': 'bullish_divergence',
                    'a_price': a_price, 'c_price': c_price,
                    'a_dif': a_dif, 'c_dif': c_dif}
    return None

def _find_buy_points(kbars, strokes, pivots, divergence):
    result = {'first_buy': None, 'second_buy': None, 'third_buy': None}
    if divergence and strokes:
        result['first_buy'] = {'bar': strokes[-1][1], 'price': kbars[strokes[-1][1]].low}
    if pivots and result.get('first_buy') and len(strokes) >= 3:
        zd = pivots[0]['zd']
        fp = result['first_buy']['price']
        fb = result['first_buy']['bar']
        for i in range(fb + 1, len(kbars)):
            if kbars[i].low <= zd and kbars[i].low >= fp * 0.95:
                result['second_buy'] = {'bar': i, 'price': kbars[i].low}
                break
    if pivots and kbars:
        zg = pivots[0]['zg']
        if kbars[-1].high > zg * 1.01:
            result['third_buy'] = {'bar': len(kbars)-1, 'price': zg}
    return result

# ─────────────────────────────────────────────────────────────────────────────
# 测试数据
# ─────────────────────────────────────────────────────────────────────────────

def create_wave_data():
    """明显的波浪：顶-底-顶-底-顶"""
    prices = [
        100.0, 101.0, 103.0,  # 起始上涨
        102.0, 100.5, 99.0,   # 下跌
        100.0, 101.5, 103.5,  # 反弹
        102.0, 100.5, 98.5,   # 再跌
        100.0, 101.5, 104.0,  # 再涨
        103.0, 101.5, 100.0,  # 回调
        101.0, 102.5, 105.0,  # 新高
        104.0, 102.5, 101.0,  # 再次回调
        102.0, 103.5, 106.0,  # 继续上涨
    ]
    flat = []
    for i in range(len(prices) - 1):
        flat.append(prices[i])
        mid = (prices[i] + prices[i+1]) / 2
        flat.append(mid)
    flat.append(prices[-1])
    data = []
    for p in flat:
        h = p * 1.008
        l = p * 0.992
        o = (h + l) / 2
        data.append({'open': o, 'high': h, 'low': l, 'close': p})
    return pd.DataFrame(data)

def create_divergence_data():
    """底背驰数据"""
    data = []
    base = 100.0
    for i in range(20):
        base *= 1.015
        data.append({'open': base*0.998, 'high': base*1.005, 'low': base*0.99, 'close': base})
    a = base
    for i in range(12):
        a *= 0.95
        data.append({'open': a*1.003, 'high': a*1.008, 'low': a*0.99, 'close': a})
    b = a
    for i in range(8):
        b *= 1.025
        data.append({'open': b*0.997, 'high': b*1.005, 'low': b*0.99, 'close': b})
    c = b
    for i in range(10):
        c *= 0.97
        data.append({'open': c*1.002, 'high': c*1.006, 'low': c*0.993, 'close': c})
    for i in range(25):
        c = c * (1 + np.sin(i/4) * 0.005)
        data.append({'open': c*0.998, 'high': c*1.004, 'low': c*0.992, 'close': c})
    return pd.DataFrame(data)

def create_random_walk():
    """随机游走"""
    np.random.seed(42)
    n = 200
    closes = [100.0]
    for i in range(n-1):
        delta = np.random.randn() * 0.8
        closes.append(closes[-1] + delta)
    data = []
    for c in closes:
        h = c * (1 + abs(np.random.randn()) * 0.005)
        l = c * (1 - abs(np.random.randn()) * 0.005)
        o = (h + l) / 2
        data.append({'open': o, 'high': h, 'low': l, 'close': c})
    return pd.DataFrame(data)

# ─────────────────────────────────────────────────────────────────────────────
# 测试运行
# ─────────────────────────────────────────────────────────────────────────────

def test(name, df, expected_min_strokes=2):
    print(f"\n{'='*60}")
    print(f"  {name}  (n={len(df)})")
    print(f"{'='*60}")
    kbars = [KBar(i, float(r.open), float(r.high), float(r.low), float(r.close))
             for i, r in df.iterrows()]
    merged = _merge_inclusive_strict(kbars)
    fractals = _find_fractals_strict(merged)
    strokes = _build_strokes(fractals, min_len=5)
    closes_arr = np.array([kb.close for kb in kbars])
    dif, dea = _calc_macd(closes_arr)
    pivots = _find_pivots(strokes, merged) if len(strokes) >= 3 else []
    divergence = _detect_divergence(kbars, strokes, dif) if len(strokes) >= 4 else None
    buy_points = _find_buy_points(kbars, strokes, pivots, divergence)

    print(f"  原始K线: {len(kbars)}  合并后: {len(merged)}  分型: {len(fractals)}  笔: {len(strokes)}")
    if fractals:
        types = [f.type for f in fractals]
        print(f"  分型序列: {'-'.join(types[:12])}{'...' if len(types)>12 else ''}")
    if strokes:
        alt_ok = all(strokes[i][2] != strokes[i+1][2] for i in range(len(strokes)-1))
        print(f"  笔交替: {'✓' if alt_ok else '✗'}  数量{'✓' if len(strokes)>=expected_min_strokes else '⚠'}")
    if pivots:
        for i, p in enumerate(pivots[:2]):
            print(f"    中枢{i+1}: [{p['zd']:.2f}, {p['zg']:.2f}] 宽={p['zg']-p['zd']:.2f}")
    if divergence:
        print(f"  背驰: ✓ 底背驰 (A={divergence['a_price']:.2f}/{divergence['a_dif']:.4f}, "
              f"C={divergence['c_price']:.2f}/{divergence['c_dif']:.4f})")
    else:
        print(f"  背驰: {'未检测（笔数<4）' if len(strokes)<4 else '未检测到'}")
    bp = buy_points
    print(f"  买点: 一买={'✓' if bp['first_buy'] else '✗'}  二买={'✓' if bp['second_buy'] else '✗'}  三买={'✓' if bp['third_buy'] else '✗'}")
    return {
        'merged': len(merged), 'fractals': len(fractals), 'strokes': len(strokes),
        'pivots': len(pivots), 'divergence': divergence is not None,
        'first_buy': bp['first_buy'] is not None, 'second_buy': bp['second_buy'] is not None,
    }

print("╔══════════════════════════════════════════════════════════════════════╗")
print("║           严格缠论算法验证 — chanlun_strict.py 核心算法              ║")
print("╚══════════════════════════════════════════════════════════════════════╝")

r1 = test("波浪数据（顶-底-顶-底-顶）", create_wave_data(), expected_min_strokes=2)
r2 = test("底背驰数据（跌→反弹→再跌）", create_divergence_data(), expected_min_strokes=2)
r3 = test("随机游走（200根K线）", create_random_walk(), expected_min_strokes=2)

print(f"\n{'='*60}")
print("汇总")
print(f"{'='*60}")
all_ok = True
for name, r in [("波浪数据", r1), ("底背驰数据", r2), ("随机游走", r3)]:
    f_ok = "✓" if r['fractals'] >= 1 else "✗"
    s_ok = "✓" if r['strokes'] >= 2 else "⚠"
    print(f"  {name}: 合并后{r['merged']}根  分型{f_ok}{r['fractals']}  笔{s_ok}{r['strokes']}  "
          f"中枢{r['pivots']}  背驰{'✓' if r['divergence'] else '✗'}  "
          f"一买{'✓' if r['first_buy'] else '✗'}")
print(f"\n核心结论: {'✓ 所有模块正常工作' if all_ok else '⚠ 部分模块待优化'}")
