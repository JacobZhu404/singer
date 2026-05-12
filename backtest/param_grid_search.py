#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
strong_stock 策略参数网格搜索
测试不同阈值和参数组合，找到最优参数
"""
import sys, os, json, glob
sys.path.insert(0, '/Users/jacob/personal')
sys.path.insert(0, '/Users/jacob/personal/stock_screener')

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import logging
logging.basicConfig(level=logging.WARNING, format='%(asctime)s %(message)s', stream=sys.stdout)

from stock_screener.utils.indicators import (
    calc_volume_ratio, calc_macd, is_red_candle, detect_gap_up
)

KL_DIR = '/Users/jacob/personal/stock_screener/data/cache/klines'
HOLD = [3, 5, 10, 30]


def load_df(code):
    p = f'{KL_DIR}/{code}.csv'
    if not os.path.exists(p):
        return None
    df = pd.read_csv(p)
    required = ['date', 'open', 'high', 'low', 'close', 'vol']
    if not all(c in df.columns for c in required):
        return None
    df['date_str'] = pd.to_datetime(df['date']).dt.strftime('%Y%m%d')
    df = df.sort_values('date').reset_index(drop=True)
    return df if len(df) >= 60 else None


def check_signal_with_params(df_hist, thresholds):
    """根据传入的参数检查信号"""
    try:
        close = df_hist['close']
        high = df_hist['high']
        low = df_hist['low']
        vol = df_hist['vol']
        open_ = df_hist['open'] if 'open' in df_hist else close
        i = len(df_hist) - 1
        if i < 30:
            return None

        score, signals = 0, []
        vr = calc_volume_ratio(vol, 5)
        red = is_red_candle(open_, close)
        dif, dea, macd_bar = calc_macd(close)
        gap_up = detect_gap_up(high, low, open_, close)

        # 参数化条件
        vr_threshold = thresholds.get('vr_threshold', 1.5)
        up_down_threshold = thresholds.get('up_down_threshold', 1.5)
        add_gap_up = thresholds.get('add_gap_up', True)
        add_macd = thresholds.get('add_macd', True)
        max_cumulative_gain = thresholds.get('max_cumulative_gain', 999.0)

        if not pd.isna(vr.iloc[i]) and vr.iloc[i] > vr_threshold and red.iloc[i]:
            signals.append(f"放量上涨(量比{vr.iloc[i]:.1f}x)")
            score += 20

        n = min(10, i + 1)
        up_vol = down_vol = 0.0
        for j in range(max(0, i - n + 1), i + 1):
            if pd.isna(red.iloc[j]):
                continue
            v = float(vol.iloc[j])
            if red.iloc[j]:
                up_vol += v
            else:
                down_vol += v
        if down_vol > 0 and up_vol / (down_vol + 1e-8) > up_down_threshold:
            signals.append(f"红肥绿瘦(涨缩量比{up_vol/down_vol:.1f})")
            score += 20

        if i >= 4 and all(red.iloc[i - k] for k in range(5)) and \
           all(not pd.isna(close.pct_change().iloc[i - k]) and
               abs(float(close.pct_change().iloc[i - k])) <= 3.0
               for k in range(5)):
            # 累计涨幅限制
            cumulative_gain = (close.iloc[i] / close.iloc[i-4] - 1) * 100
            if cumulative_gain < max_cumulative_gain:
                signals.append("五连小阳")
                score += 20
        elif add_gap_up and gap_up.iloc[i]:
            signals.append("跳空缺口(今低>昨高)")
            score += 20

        if add_macd and not pd.isna(dif.iloc[i]) and dif.iloc[i] > 0:
            signals.append("MACD零轴以上")
            score += 20

        # 动态阈值
        score_threshold = thresholds.get('score_threshold', 30)
        if score < score_threshold:
            return None

        return {
            'score': min(score, 100),
            'signals': signals,
            'price': float(close.iloc[i])
        }
    except Exception as e:
        return None


def calc_returns(df, idx, period):
    entry = float(df.iloc[idx]['close'])
    end_idx = idx + period
    if end_idx >= len(df):
        return None, None
    prices = df.iloc[idx+1:end_idx+1]['close'].values.astype(float)
    if len(prices) == 0:
        return None, None
    ret = (prices[-1] - entry) / entry * 100
    peak = entry
    max_dd = 0.0
    for p in prices:
        if p > peak:
            peak = p
        dd = (peak - p) / peak * 100
        if dd > max_dd:
            max_dd = dd
    return ret, max_dd


def run_backtest_for_params(params, stock_dfs, dates):
    """对给定参数运行回测，返回表现指标"""
    strategy_trades = []
    
    for tdate in dates:
        week_hits = []
        for code, (name, df) in stock_dfs.items():
            if tdate not in df['date_str'].values:
                continue
            idx = df[df['date_str'] == tdate].index[0]
            if idx < 30:
                continue
            
            hist = df.iloc[:idx+1].copy()
            result = check_signal_with_params(hist, params)
            if not result:
                continue
            
            entry_price = result['price']
            rets, dds = {}, {}
            for p in HOLD:
                r, d = calc_returns(df, idx, p)
                if r is not None:
                    rets[p] = r
                    dds[p] = d
            
            week_hits.append({
                'date': tdate, 'code': code, 'name': name,
                'price': entry_price,
                'score': result['score'],
                'signals': result['signals'],
                'returns': rets, 'drawdowns': dds
            })
        
        # 每周取 Top10
        top10 = sorted(week_hits, key=lambda x: x['score'], reverse=True)[:10]
        strategy_trades.extend(top10)
    
    # 计算表现指标
    if not strategy_trades:
        return None
    
    result = {'total': len(strategy_trades), 'periods': {}}
    for p in HOLD:
        rs = [t['returns'][p] for t in strategy_trades if p in t['returns']]
        ds = [t['drawdowns'][p] for t in strategy_trades if p in t['drawdowns']]
        if rs:
            wins = sum(1 for r in rs if r > 0)
            wr = wins / len(rs) * 100
            avg_r = float(np.mean(rs))
            avg_d = float(np.mean(ds)) if ds else 0.0
            max_r = float(np.max(rs))
            max_l = float(np.min(rs))
            result['periods'][p] = {
                'win_rate': round(wr, 1),
                'avg_return': round(avg_r, 2),
                'avg_drawdown': round(avg_d, 2),
                'max_return': round(max_r, 2),
                'max_loss': round(max_l, 2),
                'total': len(rs)
            }
    
    return result


def main():
    print(f"开始加载数据 ({datetime.now().strftime('%H:%M:%S')})...")
    
    # 加载股票列表（取前500只加快速度）
    with open('/Users/jacob/personal/stock_screener/data/cache/stocks.json') as f:
        stock_map = {str(s['代码']): s['名称'] for s in json.load(f) if s.get('代码')}
    keys = list(stock_map.keys())[:500]
    stock_map = {k: stock_map[k] for k in keys}
    
    # 加载K线
    stock_dfs = {}
    for code in stock_map:
        df = load_df(code)
        if df is not None:
            stock_dfs[code] = (stock_map[code], df)
    print(f"股票池: {len(stock_dfs)} 只")
    
    # 生成信号日（每周一个）
    sh_df = load_df('000001')
    sh_df['date'] = pd.to_datetime(sh_df['date'])
    start, end = datetime(2025, 10, 1), datetime(2026, 4, 24)
    mask = (sh_df['date'] >= start) & (sh_df['date'] <= end)
    df_s = sh_df[mask].copy()
    df_s['yw'] = (df_s['date'].dt.isocalendar().year.astype(str) + '_' +
                  df_s['date'].dt.isocalendar().week.astype(str))
    df_s['wd'] = df_s['date'].dt.weekday
    dates = []
    for _, g in df_s.groupby('yw'):
        wed = g[g['wd'] == 2]
        dates.append(wed.iloc[0]['date'].strftime('%Y%m%d') if not wed.empty
                      else g.iloc[-1]['date'].strftime('%Y%m%d'))
    print(f"信号日: {len(dates)} 个\n")
    
    # 参数网格
    param_grid = [
        # 低风险：高阈值
        {'score_threshold': 50, 'vr_threshold': 1.5, 'up_down_threshold': 1.5, 'add_gap_up': True, 'add_macd': True, 'max_cumulative_gain': 12.0},
        {'score_threshold': 50, 'vr_threshold': 1.5, 'up_down_threshold': 2.0, 'add_gap_up': True, 'add_macd': True, 'max_cumulative_gain': 12.0},
        {'score_threshold': 50, 'vr_threshold': 2.0, 'up_down_threshold': 1.5, 'add_gap_up': True, 'add_macd': True, 'max_cumulative_gain': 12.0},
        # 中风险：中阈值 + 累计涨幅限制
        {'score_threshold': 40, 'vr_threshold': 1.5, 'up_down_threshold': 1.5, 'add_gap_up': True, 'add_macd': True, 'max_cumulative_gain': 10.0},
        {'score_threshold': 40, 'vr_threshold': 1.5, 'up_down_threshold': 1.5, 'add_gap_up': True, 'add_macd': True, 'max_cumulative_gain': 15.0},
        # 高风险：低阈值（当前设置）
        {'score_threshold': 30, 'vr_threshold': 1.5, 'up_down_threshold': 1.5, 'add_gap_up': True, 'add_macd': True, 'max_cumulative_gain': 999.0},
    ]
    
    print(f"开始网格搜索，共 {len(param_grid)} 组参数...")
    print(f"每只股票池 {len(stock_dfs)} 只，{len(dates)} 个信号日\n")
    
    results = []
    for i, params in enumerate(param_grid):
        print(f"[{i+1}/{len(param_grid)}] 测试: threshold={params['score_threshold']}, max_gain={params['max_cumulative_gain']}")
        result = run_backtest_for_params(params, stock_dfs, dates)
        if result:
            results.append({'params': params, 'result': result})
            print(f"  信号数: {result['total']}")
            for p in HOLD:
                if p in result['periods']:
                    pr = result['periods'][p]
                    print(f"  {p}日: 胜率{pr['win_rate']}%, 平均收益{pr['avg_return']:+.2f}%, 平均回撤{pr['avg_drawdown']:+.2f}%")
        else:
            print("  无有效信号")
        print()
    
    # 保存结果
    output_path = '/Users/jacob/personal/stock_screener/backtest/results/param_grid_search.json'
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"结果已保存: {output_path}")
    
    # 找出最优参数（按10日胜率排序）
    print("\n" + "="*100)
    print("按10日胜率排序的结果:")
    print("="*100)
    sorted_results = sorted(results, 
                          key=lambda x: x['result']['periods'].get(10, {}).get('win_rate', 0),
                          reverse=True)
    for i, r in enumerate(sorted_results):
        params = r['params']
        perf_10 = r['result']['periods'].get(10, {})
        perf_30 = r['result']['periods'].get(30, {})
        print(f"{i+1}. threshold={params['score_threshold']}, max_gain={params['max_cumulative_gain']}")
        print(f"   10日: 胜率{perf_10.get('win_rate', 0)}%, 平均收益{perf_10.get('avg_return', 0):+.2f}%")
        print(f"   30日: 胜率{perf_30.get('win_rate', 0)}%, 平均收益{perf_30.get('avg_return', 0):+.2f}%")
        print()


if __name__ == '__main__':
    main()
