#!/usr/bin/env python3
import sys, os, json
sys.path.insert(0, '/Users/jacob/personal')
sys.path.insert(0, '/Users/jacob/personal/stock_screener')

import pandas as pd
from datetime import datetime
from stock_screener.strategies.registry import STRATEGY_REGISTRY
from stock_screener.utils.indicators import calc_macd, calc_ma

KL_DIR = '/Users/jacob/personal/stock_screener/data/cache/klines'

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

# 只取最近4周、2个策略、200只股票做快速测试
sh_df = load_df('000001')
sh_df['date'] = pd.to_datetime(sh_df['date'])
start, end = datetime(2026, 3, 1), datetime(2026, 4, 24)
mask = (sh_df['date'] >= start) & (sh_df['date'] <= end)
df_s = sh_df[mask].copy()
df_s['yw'] = (df_s['date'].dt.isocalendar().year.astype(str) + '_' + df_s['date'].dt.isocalendar().week.astype(str))
df_s['wd'] = df_s['date'].dt.weekday
dates = []
for _, g in df_s.groupby('yw'):
    wed = g[g['wd'] == 2]
    dates.append(wed.iloc[0]['date'].strftime('%Y%m%d') if not wed.empty else g.iloc[-1]['date'].strftime('%Y%m%d'))
dates = sorted(dates)

print('Dates:', dates)

with open('/Users/jacob/personal/stock_screener/data/cache/stocks.json') as f:
    stock_map = {str(s['代码']): s['名称'] for s in json.load(f) if s.get('代码')}

# 只取200只
keys = list(stock_map.keys())[:200]
stock_map = {k: stock_map[k] for k in keys}

stock_dfs = {}
for code in stock_map:
    df = load_df(code)
    if df is not None:
        stock_dfs[code] = df

print('Stock pool:', len(stock_dfs))

# 只测macd_bull
strat = 'macd_bull'
for tdate in dates[:2]:
    hits = 0
    for code, df in stock_dfs.items():
        if tdate not in df['date_str'].values:
            continue
        idx = df[df['date_str'] == tdate].index[0]
        if idx < 30:
            continue
        close = df['close']
        mas = calc_ma(close, [5, 10, 20, 60])
        ma5, ma10 = mas['ma5'].iloc[idx], mas['ma10'].iloc[idx]
        dif, dea, _ = calc_macd(close)
        score = 0
        if mas['ma5'].iloc[idx-1] <= mas['ma10'].iloc[idx-1] and ma5 > ma10:
            score += 25
        elif ma5 > ma10:
            score += 15
        if ma5 > ma10 > mas['ma20'].iloc[idx] > mas['ma60'].iloc[idx]:
            score += 20
        if dif.iloc[idx] > 0:
            score += 15
        if dea.iloc[idx] > 0:
            score += 15
        if dif.iloc[idx] > dea.iloc[idx]:
            score += 15
        if score >= 50:
            hits += 1
    print(f'{tdate} {strat}: {hits} hits')

print('Done')
