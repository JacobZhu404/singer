#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
5日线强势策略回测（优化版）
策略逻辑（收紧后）：
1. 5/20日均线多头排列（MA5 > MA20）
2. 只保留高胜率形态：阳线重新站上5日线 或 沿5日线稳健上涨
3. 必须成交量放大确认（vol_ratio >= 1.3）
4. MACD柱>0（确认上升趋势）
5. RSI < 70（非超买）
6. 最低分数从30提高到50
"""

import os
import sys
import glob
import json
import logging
import numpy as np
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "cache", "klines")
HOLD_PERIODS = [2, 5, 10, 30]


def load_cached_kline(code):
    """加载本地缓存的K线数据"""
    path = os.path.join(_CACHE_DIR, "%s.csv" % code)
    if not os.path.exists(path):
        return pd.DataFrame()
    try:
        df = pd.read_csv(path, encoding="utf-8")
        df["date"] = pd.to_datetime(df["date"])
        return df.sort_values("date").reset_index(drop=True)
    except Exception as e:
        logger.debug("加载 %s 失败: %s" % (code, str(e)))
        return pd.DataFrame()


def check_strong_5day(df, idx):
    """
    检查是否满足5日线强势条件（优化版）
    返回：(是否满足, 信号描述, 分数)
    """
    if idx < 20 or idx >= len(df) - 1:
        return False, "", 0
    
    close = df["close"]
    open_ = df["open"] if "open" in df.columns else close
    high = df["high"]
    low = df["low"]
    vol = df["vol"]
    
    # 计算均线
    ma5 = close.rolling(5).mean()
    ma20 = close.rolling(20).mean()
    
    # 条件1：5/20多头排列（必须严格成立）
    if pd.isna(ma5.iloc[idx]) or pd.isna(ma20.iloc[idx]):
        return False, "", 0
    if ma5.iloc[idx] <= ma20.iloc[idx]:
        return False, "", 0
    
    # 条件2：昨日阴线破5日线，但今日阳线重新站上（核心信号）
    today = df.iloc[idx]
    yesterday = df.iloc[idx - 1]
    
    is_yesterday_yin = yesterday["close"] < yesterday["open"]
    is_today_yang = today["close"] > today["open"]
    
    score = 0
    signals = []
    
    # 只保留最强形态：阳线重新站上5日线
    if is_yesterday_yin and yesterday["close"] < ma5.iloc[idx - 1]:
        if is_today_yang and today["close"] > ma5.iloc[idx]:
            # 今日阳线实体越大越强
            yang_size = (today["close"] - today["open"]) / today["open"] * 100
            if yang_size > 1.0:  # 阳线实体>1%
                signals.append("阳线强势站上5日线(实体%.1f%%)" % yang_size)
                score += 40
            else:
                signals.append("阳线站上5日线")
                score += 30
    
    # 形态B：连续阳线沿5日线稳健上涨（非疯狂拉升）
    if is_today_yang and not is_yesterday_yin:
        if today["close"] > ma5.iloc[idx]:
            # 检查是否沿5日线稳健上涨（5日涨幅<15%）
            if idx >= 5:
                gain_5d = (today["close"] - close.iloc[idx-5]) / close.iloc[idx-5] * 100
                if gain_5d < 15:  # 非疯狂拉升
                    signals.append("沿5日线稳健上涨")
                    score += 30
    
    if not signals:
        return False, "", 0
    
    # 过滤1：必须成交量放大（确认买盘）
    if idx >= 5:
        vol_ma5 = vol.iloc[idx-5:idx].mean()
        if vol_ma5 <= 0:
            return False, "", 0
        vol_ratio = vol.iloc[idx] / vol_ma5
        if vol_ratio < 1.3:
            return False, "", 0  # 成交量未放大，跳过
        if vol_ratio >= 1.5:
            signals.append("成交量放大%.1fx" % vol_ratio)
            score += 15
        else:
            signals.append("成交量温和放大%.1fx" % vol_ratio)
            score += 10
    
    # 过滤2：MACD柱>0（确认上升趋势）
    try:
        from stock_screener.utils.indicators import calc_macd
        _, _, macd_bar = calc_macd(close)
        if macd_bar.iloc[idx] <= 0:
            return False, "", 0  # MACD柱<=0，趋势不够强
        if idx >= 1 and macd_bar.iloc[idx] > macd_bar.iloc[idx-1]:
            signals.append("MACD柱放大")
            score += 10
    except:
        pass
    
    # 过滤3：RSI < 70（非超买）
    try:
        from stock_screener.utils.indicators import calc_rsi
        rsi = calc_rsi(close, 14).iloc[idx]
        if rsi > 70:
            return False, "", 0  # 超买，跳过
        if 50 <= rsi <= 70:
            signals.append("RSI强势区间(%.0f)" % rsi)
            score += 10
    except:
        pass
    
    # 过滤4：今日最低价不破5日线太多（支撑强）
    if low.iloc[idx] < ma5.iloc[idx] * 0.97:
        return False, "", 0  # 下影线太长，支撑不稳
    
    return True, ",".join(signals), score


def calc_future_returns(df, entry_idx):
    """计算未来各持有期收益和最大回撤"""
    entry_price = float(df.iloc[entry_idx]["close"])
    returns, exits, drawdowns = {}, {}, {}
    
    for period in HOLD_PERIODS:
        end_idx = entry_idx + period
        if end_idx >= len(df):
            continue
        
        prices = df.iloc[entry_idx + 1:end_idx + 1]["close"].values.astype(float)
        if len(prices) == 0:
            continue
        
        exit_price = prices[-1]
        exits[period] = exit_price
        returns[period] = (exit_price - entry_price) / entry_price * 100
        
        # 最大回撤
        peak = entry_price
        max_dd = 0.0
        for p in prices:
            if p > peak:
                peak = p
            dd = (peak - p) / peak * 100
            if dd > max_dd:
                max_dd = dd
        drawdowns[period] = max_dd
    
    return returns, exits, drawdowns


def backtest_single_stock(code, min_score=50):
    """回测单只股票"""
    df = load_cached_kline(code)
    if df.empty or len(df) < 30:
        return []
    
    trades = []
    for idx in range(20, len(df) - 1):  # 留出空间给未来收益计算
        qualified, signal_desc, score = check_strong_5day(df, idx)
        if not qualified or score < min_score:
            continue
        
        returns, exits, drawdowns = calc_future_returns(df, idx)
        if not returns:
            continue
        
        trades.append({
            "code": code,
            "buy_date": df.iloc[idx]["date"].strftime("%Y-%m-%d"),
            "buy_price": float(df.iloc[idx]["close"]),
            "score": score,
            "signals": signal_desc,
            "returns": returns,
            "drawdowns": drawdowns,
        })
    
    return trades


def run_backtest(max_workers=10, max_stocks=None):
    """运行回测"""
    # 获取所有缓存的股票代码
    csv_files = glob.glob(os.path.join(_CACHE_DIR, "*.csv"))
    codes = [os.path.basename(f).replace(".csv", "") for f in csv_files]
    
    if max_stocks:
        codes = codes[:max_stocks]
    
    logger.info("开始回测，共 %d 只股票" % len(codes))
    
    all_trades = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(backtest_single_stock, code): code for code in codes}
        for idx, future in enumerate(as_completed(futures), 1):
            try:
                trades = future.result()
                all_trades.extend(trades)
                if idx % 100 == 0:
                    logger.info("进度: %d/%d, 已找到 %d 个交易信号" % (idx, len(codes), len(all_trades)))
            except Exception as e:
                logger.error("回测失败: %s" % str(e))
    
    # 统计结果
    logger.info("回测完成，共找到 %d 个交易信号" % len(all_trades))
    
    if not all_trades:
        print("未发现任何交易信号，请检查策略条件是否过于严格")
        return
    
    # 按持有期统计
    for period in HOLD_PERIODS:
        period_trades = [t for t in all_trades if period in t["returns"]]
        if not period_trades:
            continue
        
        returns = [t["returns"][period] for t in period_trades]
        drawdowns = [t["drawdowns"][period] for t in period_trades]
        
        wins = sum(1 for r in returns if r > 0)
        win_rate = wins / len(returns) * 100
        avg_return = np.mean(returns)
        avg_drawdown = np.mean(drawdowns)
        
        print("\n=== 持有%d天 ===" % period)
        print("交易次数: %d" % len(returns))
        print("胜率: %.1f%%" % win_rate)
        print("平均收益: %.2f%%" % avg_return)
        print("平均最大回撤: %.2f%%" % avg_drawdown)
        print("最佳交易: %.2f%%" % max(returns))
        print("最差交易: %.2f%%" % min(returns))
    
    # 保存详细结果
    output_file = "backtest_results_5day_strong.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(all_trades[:100], f, ensure_ascii=False, indent=2)  # 只保存前100条
    logger.info("详细结果已保存到 %s" % output_file)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="5日线强势策略回测（优化版）")
    parser.add_argument("--max-stocks", type=int, default=None, help="最大回测股票数（测试用）")
    parser.add_argument("--max-workers", type=int, default=10, help="并行线程数")
    args = parser.parse_args()
    
    run_backtest(max_workers=args.max_workers, max_stocks=args.max_stocks)
