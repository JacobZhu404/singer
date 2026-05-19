#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
歌者策略回测脚本
回测过去6个月数据 (2025-10 ~ 2026-04), 覆盖约26周
"""
import sys, os, json, glob
sys.path.insert(0, '/Users/jacob/personal')
sys.path.insert(0, '/Users/jacob/personal/stock_screener')

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import logging
logging.basicConfig(level=logging.WARNING, format='%(asctime)s %(message)s', stream=sys.stdout)

from stock_screener.strategies.registry import STRATEGY_REGISTRY
from stock_screener.utils.indicators import (
    calc_risk_flags, calc_macd, calc_rsi, calc_bollinger,
    calc_ma, calc_volume_ratio, td_sequential_count, calc_skdj
)

HOLD = [3, 5, 10, 30]
KL_DIR = '/Users/jacob/personal/stock_screener/data/cache/klines'


def load_df(code):
    p = f'{KL_DIR}/{code}.csv'
    if not os.path.exists(p):
        return None
    df = pd.read_csv(p)
    # 检查必要列是否存在
    required = ['date', 'open', 'high', 'low', 'close', 'vol']
    if not all(c in df.columns for c in required):
        return None
    df['date_str'] = pd.to_datetime(df['date']).dt.strftime('%Y%m%d')
    df = df.sort_values('date').reset_index(drop=True)
    return df if len(df) >= 60 else None


def check_signal(code, name, df_hist, strat):
    """检查某只股票在历史某日是否符合策略条件"""
    try:
        close = df_hist['close']
        high  = df_hist['high']
        low   = df_hist['low']
        vol   = df_hist['vol']
        open_ = df_hist['open'] if 'open' in df_hist else close
        i = len(df_hist) - 1
        if i < 30:
            return None
        score, signals = 0, []

        if strat == 'macd_bull':
            dif, dea, _ = calc_macd(close)
            mas = calc_ma(close, [5, 10, 20, 60])
            ma5, ma10, ma20, ma60 = (mas[k].iloc[i] for k in ('ma5', 'ma10', 'ma20', 'ma60'))
            if mas['ma5'].iloc[i-1] <= mas['ma10'].iloc[i-1] and ma5 > ma10:
                signals.append('MA金叉'); score += 25
            elif ma5 > ma10:
                signals.append('MA多头'); score += 15
            if ma5 > ma10 > ma20 > ma60:
                signals.append('均线多头'); score += 20
            if dif.iloc[i] > 0:
                signals.append('DIF零轴上'); score += 15
            if dea.iloc[i] > 0:
                signals.append('DEA零轴上'); score += 15
            if dif.iloc[i] > dea.iloc[i]:
                signals.append('MACD金叉'); score += 15
            if score < 50:
                return None

        elif strat == 'strong_stock':
            vr = calc_volume_ratio(vol, 5)
            red = close > open_
            dif, _, _ = calc_macd(close)
            if vr.iloc[i] > 1.5 and red.iloc[i]:
                signals.append('放量上涨'); score += 20
            up_vol = sum(vol.iloc[i-j] for j in range(min(10, i+1)) if red.iloc[i-j])
            dn_vol = sum(vol.iloc[i-j] for j in range(min(10, i+1)) if not red.iloc[i-j])
            if dn_vol > 0 and up_vol / dn_vol > 1.5:
                signals.append('红肥绿瘦'); score += 20
            if i >= 4:
                pct = close.pct_change() * 100
                if all(red.iloc[i-k] for k in range(5)) and all(abs(pct.iloc[i-k]) <= 3 for k in range(5)):
                    signals.append('五连小阳'); score += 20
            if dif.iloc[i] > 0:
                signals.append('MACD零轴'); score += 20
            if score < 50:
                return None

        elif strat == 'td_sequential':
            td = td_sequential_count(close, high=high, low=low)
            cnt = int(td.iloc[i])
            if cnt == 9:
                signals.append('九转完成'); score += 50
            elif cnt in (7, 8):
                signals.append(f'九转{cnt}'); score += 25
            else:
                return None
            if i >= 5 and vol.iloc[i] > vol.iloc[i-5:i].mean() * 1.2:
                signals.append('量能确认'); score += 15

        elif strat == 'right_side':
            mas = calc_ma(close, [5, 20, 60])
            vr = calc_volume_ratio(vol, 5).iloc[i]
            rsi = calc_rsi(close, 14).iloc[i]
            dif, _, _ = calc_macd(close)
            c = close.iloc[i]
            if i >= 20 and c > high.iloc[i-20:i].max():
                signals.append('20日新高'); score += 25
            if vr > 1.5:
                signals.append(f'放量{vr:.1f}x'); score += 20
            if c > mas['ma60'].iloc[i]:
                signals.append('站上MA60'); score += 15
            if 50 <= rsi <= 72:
                signals.append(f'RSI强势区{rsi:.0f}'); score += 10
            if dif.iloc[i] > 0:
                signals.append('MACD零轴'); score += 10
            if score < 40:
                return None

        elif strat == 'rsi_oversold':
            rsi = calc_rsi(close, 14)
            rv, rp = rsi.iloc[i], rsi.iloc[i-1]
            if rv > 60:
                return None
            if rv < 30:
                signals.append(f'RSI({rv:.0f})<30'); score += 50
            elif rp < 30 <= rv < 40:
                signals.append('RSI底部回升'); score += 25
            elif 30 <= rv < 40:
                signals.append(f'RSI({rv:.0f})低位'); score += 15
            if close.iloc[i] < close.rolling(20).mean().iloc[i]:
                signals.append('价格<20日均线'); score += 10
            if score < 40:
                return None

        elif strat == 'bollinger_bands':
            mid = close.rolling(20).mean()
            std = close.rolling(20).std()
            lower = (mid - 2 * std).iloc[i]
            if pd.isna(lower) or lower == 0:
                return None
            pct = (close.iloc[i] - lower) / lower * 100
            if pct <= 3:
                signals.append(f'触布林下{pct:.1f}%'); score += 40
            if close.iloc[i-1] <= lower * 1.02 and close.iloc[i] > close.iloc[i-1]:
                signals.append('布林下反弹'); score += 30
            if score < 40:
                return None

        elif strat == 'volume_breakout':
            vm20 = vol.rolling(20).mean().iloc[i]
            if pd.isna(vm20) or vm20 <= 0:
                return None
            vr = vol.iloc[i] / vm20
            h30 = close.iloc[max(0, i-30):i].max()
            if vr >= 2.0:
                signals.append(f'量比{vr:.1f}x'); score += 35
            elif vr >= 1.5:
                signals.append(f'温和放量{vr:.1f}x'); score += 20
            if close.iloc[i] > h30:
                signals.append('突破30日高点'); score += 30
            if score < 40:
                return None

        elif strat == 'chan20':
            dif, dea, _ = calc_macd(close)
            sk, sd = calc_skdj(close, high, low)
            mas = calc_ma(close, [5, 10, 20])
            crosses = []
            for ci in range(1, len(dif)):
                if pd.isna(dif.iloc[ci]) or pd.isna(dea.iloc[ci]):
                    continue
                if dif.iloc[ci - 1] <= dea.iloc[ci - 1] and dif.iloc[ci] > dea.iloc[ci]:
                    if dif.iloc[ci] < 0 and dea.iloc[ci] < 0:
                        crosses.append(ci)
            # tightened
            if len(crosses) >= 2 and (i - crosses[-1]) <= 3:
                signals.append('MACD零轴下二次金叉'); score += 40
            elif len(crosses) >= 1 and (i - crosses[-1]) <= 2:
                signals.append('MACD零轴下金叉'); score += 25
            else:
                return None
            skv, sdv = sk.iloc[i], sd.iloc[i]
            skp, sdp = sk.iloc[i - 1], sd.iloc[i - 1]
            if skp <= sdp and skv > sdv:
                if skv < 25:
                    signals.append('SKDJ低位金叉'); score += 30
                else:
                    signals.append('SKDJ金叉'); score += 15
            if skv < 20:
                signals.append('SKDJ超卖'); score += 15
            elif skv < 25:
                signals.append('SKDJ低位'); score += 10
            ma5 = mas['ma5'].iloc[i]
            if not pd.isna(ma5) and close.iloc[i] > ma5:
                signals.append('站上5日线'); score += 10
            vr = calc_volume_ratio(vol, 5).iloc[i]
            if vr > 1.2:
                signals.append('温和放量'); score += 5
            if score < 55:
                return None

        elif strat == 'chanlun_strict':
            from stock_screener.strategies.chanlun_strict import _analyze, _compute_score
            analysis = _analyze(df_hist)
            score, signals, _ = _compute_score(analysis) if analysis else (0, [], {})
            if score < 40:
                return None

        elif strat == 'golden_cross':
            dif, dea, _ = calc_macd(close)
            mas = calc_ma(close, [5, 10, 20])
            rsi = calc_rsi(close, 14)
            m5 = float(mas['ma5'].iloc[i])
            m10 = float(mas['ma10'].iloc[i])
            m20 = float(mas['ma20'].iloc[i])
            m5_prev = float(mas['ma5'].iloc[i-1]) if i >= 1 else m5
            m10_prev = float(mas['ma10'].iloc[i-1]) if i >= 1 else m10
            if any(pd.isna(x) for x in [m5, m10, m20]):
                return None
            if m5 > m10 and m5_prev <= m10_prev:
                signals.append('MA金叉'); score += 40
            elif m5 > m10:
                signals.append('MA多头'); score += 20
            if m5 > m10 > m20:
                signals.append('均线多头'); score += 25
            c = float(close.iloc[i])
            if c > m5:
                signals.append('站上MA5'); score += 15
            r = float(rsi.iloc[i]) if not pd.isna(rsi.iloc[i]) else 50
            if 50 <= r <= 65:
                signals.append(f'RSI确认({r:.0f})'); score += 10
            elif r < 50:
                score -= 10
            d = float(dif.iloc[i]) if not pd.isna(dif.iloc[i]) else 0
            if d > 0:
                signals.append('MACD零轴上'); score += 10
            if score < 70:
                return None

        elif strat == 'chanlun':
            from stock_screener.strategies.chanlun import _chanlun_score
            score, signals, _ = _chanlun_score(df_hist)
            if score < 40:
                return None

        else:
            return None

        # 风险过滤
        pct_chg = close.pct_change() * 100
        flags = calc_risk_flags(close, high, low, vol, pct_chg)
        danger_types = {'rsi_overbought', 'macd_death_cross', 'macd_top_div',
                         'bollinger_upper', 'td_sell', 'ma_empty'}
        danger = any(f.get('level') == 'danger' or f.get('type') in danger_types for f in flags)
        if danger:
            return None

        return {
            'code': code, 'name': name, 'strat': strat,
            'date': df_hist['date_str'].iloc[-1],
            'price': float(close.iloc[i]),
            'score': min(score, 100),
            'signals': signals
        }
    except Exception as e:
        return None


def calc_returns(df, idx, period):
    """计算未来N日收益和最大回撤"""
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


def run_backtest():
    # 获取交易日 (半年=26周)
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

    # 策略列表
    strategies = list(STRATEGY_REGISTRY.keys())

    # 加载股票列表
    with open('/Users/jacob/personal/stock_screener/data/cache/stocks.json') as f:
        stock_map = {str(s['代码']): s['名称'] for s in json.load(f) if s.get('代码')}
    # 快速回测：只取前200只
    keys = list(stock_map.keys())[:200]
    stock_map = {k: stock_map[k] for k in keys}

    # 加载所有K线到内存
    print(f'预加载K线 ({datetime.now().strftime("%H:%M:%S")})...')
    stock_dfs = {}
    for code in stock_map:
        df = load_df(code)
        if df is not None:
            stock_dfs[code] = (stock_map[code], df)
    print(f'股票池: {len(stock_dfs)} 只 (有60+条数据)')
    print(f'信号日: {len(dates)} 个')
    print(f'策略: {len(strategies)} 个')
    print(f'总任务: {len(dates)}×{len(strategies)}×{len(stock_dfs)} = {len(dates)*len(strategies)*len(stock_dfs):,}')
    print()

    # 回测: 按信号日 × 策略 × 股票
    # 只保留每周每策略 Top10
    strategy_trades = {s: [] for s in strategies}
    total = len(dates) * len(strategies) * len(stock_dfs)
    done = 0

    for di, tdate in enumerate(dates):
        for strat in strategies:
            week_hits = []
            for code, (name, df) in stock_dfs.items():
                done += 1
                if done % 50000 == 0:
                    pct = done * 100 // total
                    print(f'  进度: {done:,}/{total:,} ({pct}%) {datetime.now().strftime("%H:%M:%S")}')

                if tdate not in df['date_str'].values:
                    continue
                idx = df[df['date_str'] == tdate].index[0]
                if idx < 30:
                    continue

                hist = df.iloc[:idx+1].copy()
                result = check_signal(code, name, hist, strat)
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

            # 每周每策略只取 Top10
            top10 = sorted(week_hits, key=lambda x: x['score'], reverse=True)[:10]
            strategy_trades[strat].extend(top10)

    # ── 汇总统计 ───────────────────────────────────────────────
    print()
    print('=' * 110)
    print(f"{'策略':<20} {'信号总数':>8}", end='')
    for p in HOLD:
        print(f"  {p}日胜率   {p}日均收益   {p}日均回撤", end='')
    print()
    print('-' * 110)

    summary = {}
    for strat in strategies:
        trades = strategy_trades[strat]
        label = STRATEGY_REGISTRY[strat]['name']
        row = {'total': len(trades), 'periods': {}}
        print(f'{label:<20} {len(trades):>8}', end='')

        for p in HOLD:
            rs = [t['returns'][p] for t in trades if p in t['returns']]
            ds = [t['drawdowns'][p] for t in trades if p in t['drawdowns']]
            if rs:
                wins = sum(1 for r in rs if r > 0)
                wr = wins / len(rs) * 100
                avg_r = float(np.mean(rs))
                avg_d = float(np.mean(ds)) if ds else 0.0
                max_r = float(np.max(rs))
                max_l = float(np.min(rs))
                print(f'  {wr:>5.1f}%   {avg_r:>+6.2f}%   {avg_d:>+6.2f}%', end='')
                row['periods'][p] = {'win_rate': wr, 'avg_return': avg_r,
                                      'avg_drawdown': avg_d, 'max_return': max_r,
                                      'max_loss': max_l, 'total': len(rs)}
            else:
                print(f'  {"N/A":>6}    {"N/A":>7}    {"N/A":>7}', end='')
                row['periods'][p] = {}
        summary[strat] = row
        print()
    print('=' * 110)
    print(f'注: 回测区间 2025-10 至 2026-04 (约6个月), 每周取一信号日, 每策略每周Top10, 已过滤danger风险')
    print(f'     数据覆盖 {len(stock_dfs)} 只股票, {len(dates)} 个信号周')

    # 保存结果
    out_path = '/Users/jacob/personal/stock_screener/backtest/results/backtest_20260426.json'
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f'\n结果已保存: {out_path}')
    return summary


if __name__ == '__main__':
    run_backtest()
