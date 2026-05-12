#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
缠20策略参数网格搜索
预先计算指标快照，再对不同参数组合做快速判断
"""
import sys, os, json
sys.path.insert(0, '/Users/jacob/personal')
sys.path.insert(0, '/Users/jacob/personal/stock_screener')

import pandas as pd
import numpy as np
from datetime import datetime
from itertools import product
from stock_screener.utils.indicators import (
    calc_macd, calc_skdj, calc_ma, calc_volume_ratio, calc_risk_flags
)

KL_DIR = '/Users/jacob/personal/stock_screener/data/cache/klines'
HOLD = [3, 5, 10, 30]


def load_df(code):
    p = f'{KL_DIR}/{code}.csv'
    if not os.path.exists(p):
        return None
    df = pd.read_csv(p)
    df['date_str'] = pd.to_datetime(df['date']).dt.strftime('%Y%m%d')
    df = df.sort_values('date').reset_index(drop=True)
    return df if len(df) >= 60 else None


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


def main():
    # 参数网格
    param_grid = {
        'macd_double': [2, 3, 5],
        'macd_single': [1, 2, 3],
        'skdj_thresh': [20, 25, 30],
        'min_score':   [45, 50, 55],
    }

    # 加载股票列表
    with open('/Users/jacob/personal/stock_screener/data/cache/stocks.json') as f:
        stock_map = {str(s['代码']): s['名称'] for s in json.load(f) if s.get('代码')}

    # 获取交易日（同 backtest_run.py）
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

    print(f'股票池: {len(stock_map)}')
    print(f'信号日: {len(dates)}')

    # ── 预计算所有(股票, 信号日)的指标快照 ──
    print('预计算指标快照...')
    snapshots = []

    for code, name in stock_map.items():
        df = load_df(code)
        if df is None:
            continue

        close = df['close']
        high = df['high']
        low = df['low']
        vol = df['vol']

        dif, dea, _ = calc_macd(close)
        sk, sd = calc_skdj(close, high, low)
        mas = calc_ma(close, [5, 10, 20])
        vr = calc_volume_ratio(vol, 5)
        pct_chg = close.pct_change() * 100

        # MACD 零轴下金叉位置
        crosses = []
        for ci in range(1, len(dif)):
            if pd.isna(dif.iloc[ci]) or pd.isna(dea.iloc[ci]):
                continue
            if dif.iloc[ci - 1] <= dea.iloc[ci - 1] and dif.iloc[ci] > dea.iloc[ci]:
                if dif.iloc[ci] < 0 and dea.iloc[ci] < 0:
                    crosses.append(ci)

        for tdate in dates:
            if tdate not in df['date_str'].values:
                continue
            idx = df[df['date_str'] == tdate].index[0]
            if idx < 30:
                continue

            hist = df.iloc[:idx+1]
            i = idx

            # 风险标志（danger 过滤）
            flags = calc_risk_flags(
                hist['close'], hist['high'], hist['low'], hist['vol'],
                pct_chg.iloc[:idx+1]
            )
            danger_types = {
                'rsi_overbought', 'macd_death_cross', 'macd_top_div',
                'bollinger_upper', 'td_sell', 'ma_empty',
            }
            danger = any(
                f.get('level') == 'danger' or f.get('type') in danger_types
                for f in flags
            )

            # 未来收益
            rets, dds = {}, {}
            for p in HOLD:
                r, d = calc_returns(df, idx, p)
                if r is not None:
                    rets[p] = r
                    dds[p] = d

            skv, sdv = sk.iloc[i], sd.iloc[i]
            skp, sdp = sk.iloc[i-1], sd.iloc[i-1]
            ma5 = mas['ma5'].iloc[i]
            ma10 = mas['ma10'].iloc[i]
            vri = float(vr.iloc[i]) if not pd.isna(vr.iloc[i]) else 1.0
            close_i = float(close.iloc[i])

            snapshots.append({
                'code': code, 'name': name, 'date': tdate,
                'i': i, 'crosses': crosses,
                'skv': skv, 'sdv': sdv, 'skp': skp, 'sdp': sdp,
                'ma5': ma5, 'ma10': ma10,
                'vri': vri, 'close_i': close_i,
                'danger': danger,
                'rets': rets, 'dds': dds,
            })

    print(f'快照数: {len(snapshots)}')

    # ── 网格搜索 ──
    print('开始网格搜索...')
    results = []
    combinations = list(product(
        param_grid['macd_double'],
        param_grid['macd_single'],
        param_grid['skdj_thresh'],
        param_grid['min_score'],
    ))

    for combo_idx, (macd_d, macd_s, skdj_t, min_sc) in enumerate(combinations):
        # 单次窗口必须严格小于二次窗口，否则二次窗口逻辑被吞
        if macd_s >= macd_d:
            continue

        trades = []
        for snap in snapshots:
            i = snap['i']
            crosses = snap['crosses']
            score, signals = 0, []

            # MACD
            if len(crosses) >= 2 and (i - crosses[-1]) <= macd_d:
                signals.append('MACD零轴下二次金叉')
                score += 40
            elif len(crosses) >= 1 and (i - crosses[-1]) <= macd_s:
                signals.append('MACD零轴下金叉')
                score += 25
            else:
                continue

            # SKDJ
            skv, sdv, skp, sdp = snap['skv'], snap['sdv'], snap['skp'], snap['sdp']
            if skp <= sdp and skv > sdv:
                if skv < skdj_t:
                    signals.append('SKDJ低位金叉')
                    score += 30
                else:
                    signals.append('SKDJ金叉')
                    score += 15
            if skv < 20:
                signals.append('SKDJ超卖')
                score += 15
            elif skv < skdj_t:
                signals.append('SKDJ低位')
                score += 10

            # MA
            ma5, ma10 = snap['ma5'], snap['ma10']
            if not pd.isna(ma5) and snap['close_i'] > ma5:
                signals.append('站上5日线')
                score += 10
                if not pd.isna(ma10) and ma5 > ma10:
                    signals.append('MA5>MA10')
                    score += 5

            # VR
            if snap['vri'] > 1.2:
                signals.append('温和放量')
                score += 5

            if score < min_sc:
                continue
            if snap['danger']:
                continue

            trades.append({
                'score': score, 'signals': signals,
                'rets': snap['rets'], 'dds': snap['dds'],
            })

        # 统计
        row = {
            'macd_d': macd_d, 'macd_s': macd_s,
            'skdj_t': skdj_t, 'min_sc': min_sc,
            'total': len(trades),
        }
        for p in HOLD:
            rs = [t['rets'][p] for t in trades if p in t['rets']]
            ds = [t['dds'][p] for t in trades if p in t['dds']]
            if rs:
                wins = sum(1 for r in rs if r > 0)
                row[f'wr_{p}'] = round(wins / len(rs) * 100, 1)
                row[f'avg_{p}'] = round(float(np.mean(rs)), 2)
                row[f'dd_{p}'] = round(float(np.mean(ds)) if ds else 0.0, 2)
                row[f'cnt_{p}'] = len(rs)
            else:
                row[f'wr_{p}'] = 0.0
                row[f'avg_{p}'] = 0.0
                row[f'dd_{p}'] = 0.0
                row[f'cnt_{p}'] = 0

        results.append(row)
        if (combo_idx + 1) % 10 == 0 or combo_idx == len(combinations) - 1:
            print(f'  进度: {combo_idx+1}/{len(combinations)}')

    # ── 输出 ──
    results_df = pd.DataFrame(results)

    print('\n' + '=' * 100)
    print('Top 10 组合（按 10日 均收益排序）')
    print('=' * 100)
    top10 = results_df.sort_values('avg_10', ascending=False).head(10)
    cols = ['macd_d', 'macd_s', 'skdj_t', 'min_sc', 'total',
            'wr_3', 'avg_3', 'wr_5', 'avg_5', 'wr_10', 'avg_10', 'wr_30', 'avg_30']
    print(top10[cols].to_string(index=False))

    print('\n' + '=' * 100)
    print('Top 10 组合（按 10日 胜率排序）')
    print('=' * 100)
    top10_wr = results_df.sort_values('wr_10', ascending=False).head(10)
    print(top10_wr[cols].to_string(index=False))

    print('\n' + '=' * 100)
    print('Top 10 组合（按 夏普风格 = 均收益/均回撤 排序，10日）')
    print('=' * 100)
    results_df['sharpe_like_10'] = results_df['avg_10'] / (results_df['dd_10'] + 1e-8)
    top10_sh = results_df.sort_values('sharpe_like_10', ascending=False).head(10)
    cols_sh = cols + ['dd_10', 'sharpe_like_10']
    print(top10_sh[cols_sh].to_string(index=False))

    # 保存
    out_path = '/Users/jacob/personal/stock_screener/backtest/results/grid_search_chan20.json'
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    results_df.to_json(out_path, orient='records', force_ascii=False, indent=2)
    print(f'\n结果已保存: {out_path}')


if __name__ == '__main__':
    main()
