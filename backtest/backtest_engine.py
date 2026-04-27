# -*- coding: utf-8 -*-
"""
回测引擎 - 支持所有策略和缠论策略的历史回测

功能：
- 从本地 K 线缓存读取数据（无需网络）
- 支持任意持有期（默认 2/5/10/30 天）
- 每日 Top-N 选股，统计胜率、平均收益、最大回撤
- 支持卖出信号过滤（danger 级别风险标志）
"""

import os
import sys
import json
import glob
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from stock_screener.utils.indicators import calc_risk_flags
from stock_screener.strategies.registry import STRATEGY_REGISTRY

logger = logging.getLogger(__name__)

HOLD_PERIODS = [2, 5, 10, 30]
SCORE_THRESHOLD = 40

# 缓存目录（本地 CSV 文件）
_CACHE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "cache", "klines"
)


# ─── 数据类 ──────────────────────────────────────────────────────────────────

@dataclass
class BacktestTrade:
    """单次回测交易记录"""
    buy_date: str
    code: str
    name: str
    strategy: str
    buy_price: float
    score: float
    signals: List[str]
    has_risk: bool = False
    returns: Dict[int, float] = field(default_factory=dict)        # {period: return_pct}
    exit_prices: Dict[int, float] = field(default_factory=dict)
    max_drawdowns: Dict[int, float] = field(default_factory=dict)


@dataclass
class PeriodStats:
    """单个持有期的统计结果"""
    period: int
    total: int = 0
    wins: int = 0
    avg_return: float = 0.0
    avg_drawdown: float = 0.0
    win_rate: float = 0.0


@dataclass
class BacktestResult:
    """策略回测汇总"""
    strategy: str
    total_trades: int = 0
    period_stats: Dict[int, PeriodStats] = field(default_factory=dict)
    trades: List[BacktestTrade] = field(default_factory=list)


# ─── 工具函数 ─────────────────────────────────────────────────────────────────

def _load_stock_names() -> Dict[str, str]:
    """从 stocks.json 加载 代码→名称 映射"""
    path = os.path.join(os.path.dirname(_CACHE_DIR), "stocks.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {str(item.get("代码", "")): item.get("名称", "") for item in data if item.get("代码")}
    except Exception:
        return {}


def _load_cached_df(code: str) -> pd.DataFrame:
    """从本地缓存读取 K 线"""
    path = os.path.join(_CACHE_DIR, f"{code}.csv")
    if not os.path.exists(path):
        return pd.DataFrame()
    try:
        df = pd.read_csv(path, encoding="utf-8")
        df["date"] = pd.to_datetime(df["date"])
        return df.sort_values("date").reset_index(drop=True)
    except Exception:
        return pd.DataFrame()


def _calc_future_returns(df: pd.DataFrame, entry_idx: int) -> tuple:
    """
    计算未来各持有期收益和最大回撤
    Returns: (returns_dict, exit_prices_dict, drawdowns_dict)
    """
    entry_price = float(df.iloc[entry_idx]["close"])
    returns, exits, drawdowns = {}, {}, {}

    for period in HOLD_PERIODS:
        end_idx = entry_idx + period
        if end_idx >= len(df):
            continue
        prices = df.iloc[entry_idx + 1: end_idx + 1]["close"].values.astype(float)
        if len(prices) == 0:
            continue
        exit_p = prices[-1]
        exits[period] = exit_p
        returns[period] = (exit_p - entry_price) / entry_price * 100
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


def _check_sell_signal(risk_flags: list) -> bool:
    """是否有 danger 级别卖出信号"""
    danger_types = {
        "rsi_overbought", "macd_death_cross", "macd_top_div",
        "bollinger_upper", "td_sell", "ma_empty",
    }
    return any(
        f.get("level") == "danger" or f.get("type") in danger_types
        for f in risk_flags
    )


def _chanlun_check(df: pd.DataFrame) -> tuple:
    """缠论策略评分（直接使用策略模块）"""
    try:
        from stock_screener.strategies.chanlun import _chanlun_score
        score, signals, _ = _chanlun_score(df)
        return score, signals
    except Exception:
        return 0, []


def _strategy_check(strategy_name: str, df: pd.DataFrame) -> tuple:
    """
    通用策略条件检查。
    Returns: (score, signals) or (0, []) if not qualified
    """
    try:
        from stock_screener.utils.indicators import (
            calc_macd, calc_rsi, calc_bollinger, calc_ma,
            calc_volume_ratio, td_sequential_count
        )
        close = df["close"]
        high  = df["high"]
        low   = df["low"]
        vol   = df["vol"]
        open_ = df["open"] if "open" in df.columns else close
        i = len(df) - 1
        if i < 30:
            return 0, []

        score, signals = 0, []

        if strategy_name == "chanlun":
            return _chanlun_check(df)

        elif strategy_name == "macd_bull":
            dif, dea, macd_bar = calc_macd(close)
            mas = calc_ma(close, [5, 10, 20, 60])
            ma5, ma10, ma20, ma60 = (mas[k].iloc[i] for k in ("ma5", "ma10", "ma20", "ma60"))
            if mas["ma5"].iloc[i-1] <= mas["ma10"].iloc[i-1] and ma5 > ma10:
                signals.append("MA5上穿MA10金叉"); score += 25
            elif ma5 > ma10:
                signals.append("MA5>MA10多头"); score += 15
            if ma5 > ma10 > ma20 > ma60:
                signals.append("均线多头排列"); score += 20
            if dif.iloc[i] > 0: signals.append("DIF零轴以上"); score += 15
            if dea.iloc[i] > 0: signals.append("DEA零轴以上"); score += 15
            if dif.iloc[i] > dea.iloc[i]: signals.append("DIF>DEA金叉"); score += 15
            if score < 50: return 0, []

        elif strategy_name == "strong_stock":
            vol_ratio = calc_volume_ratio(vol, 5)
            red = close > open_
            dif, _, _ = calc_macd(close)
            if vol_ratio.iloc[i] > 1.5 and red.iloc[i]:
                signals.append(f"放量上涨(量比{vol_ratio.iloc[i]:.1f}x)"); score += 20
            n = min(10, i+1)
            up_vol = sum(vol.iloc[i-j] for j in range(n) if red.iloc[i-j])
            dn_vol = sum(vol.iloc[i-j] for j in range(n) if not red.iloc[i-j])
            if dn_vol > 0 and up_vol / dn_vol > 1.5:
                signals.append("红肥绿瘦"); score += 20
            if i >= 4:
                pct = close.pct_change() * 100
                if all(red.iloc[i-k] for k in range(5)) and all(abs(pct.iloc[i-k]) <= 3 for k in range(5)):
                    signals.append("五连小阳"); score += 20
            if i > 0 and low.iloc[i] > high.iloc[i-1]:
                signals.append("跳空缺口"); score += 20
            if dif.iloc[i] > 0: signals.append("MACD零轴以上"); score += 20
            if score < 40: return 0, []

        elif strategy_name == "td_sequential":
            td = td_sequential_count(close, high=high, low=low)
            dif, dea, _ = calc_macd(close)
            cnt = int(td.iloc[i])
            if cnt == 9:
                signals.append("九转完成(=9)"); score += 50
            elif cnt in (7, 8):
                signals.append(f"九转进行中({cnt})"); score += 25
            else:
                return 0, []
            if i >= 5 and vol.iloc[i] > vol.iloc[i-5:i].mean() * 1.2:
                signals.append("成交量放大确认"); score += 15

        elif strategy_name == "right_side":
            mas = calc_ma(close, [5, 20, 60])
            vr = calc_volume_ratio(vol, 5).iloc[i]
            rsi = calc_rsi(close, 14).iloc[i]
            dif, _, _ = calc_macd(close)
            c = close.iloc[i]
            if i >= 20 and c > high.iloc[i-20:i].max():
                signals.append("突破20日新高"); score += 25
            if vr > 1.5: signals.append(f"突破放量({vr:.1f}x)"); score += 20
            if c > mas["ma60"].iloc[i]: signals.append("股价站上MA60"); score += 15
            if 50 <= rsi <= 70: signals.append(f"RSI强势区({rsi:.0f})"); score += 10
            if dif.iloc[i] > 0: signals.append("MACD零轴以上"); score += 10
            if score < 40: return 0, []

        elif strategy_name == "rsi_oversold":
            rsi = calc_rsi(close, 14)
            rv, rp = rsi.iloc[i], rsi.iloc[i-1]
            if rv > 60: return 0, []
            if rv < 30: signals.append(f"RSI({rv:.0f})<30超卖"); score += 50
            elif rp < 30 <= rv < 40: signals.append("RSI底部回升"); score += 25
            elif 30 <= rv < 40: signals.append(f"RSI({rv:.0f})低位"); score += 15
            if close.iloc[i] < close.rolling(20).mean().iloc[i]:
                signals.append("价格<20日均线"); score += 10
            if score < 40: return 0, []

        elif strategy_name == "bollinger_bands":
            mid = close.rolling(20).mean()
            std = close.rolling(20).std()
            lower = (mid - 2 * std).iloc[i]
            if pd.isna(lower) or lower == 0: return 0, []
            price = close.iloc[i]
            pct = (price - lower) / lower * 100
            if pct <= 3: signals.append(f"触及布林下轨({pct:.1f}%)"); score += 40
            if close.iloc[i-1] <= lower * 1.02 and price > close.iloc[i-1]:
                signals.append("布林下轨反弹"); score += 30
            if score < 40: return 0, []

        elif strategy_name == "volume_breakout":
            vm20 = vol.rolling(20).mean().iloc[i]
            if pd.isna(vm20) or vm20 <= 0: return 0, []
            vr = vol.iloc[i] / vm20
            price = close.iloc[i]
            h30 = high.iloc[max(0, i-30):i].max()  # 突破应基于最高价
            if vr >= 2.0: signals.append(f"量比{vr:.1f}x放量"); score += 35
            elif vr >= 1.5: signals.append(f"温和放量{vr:.1f}x"); score += 20
            if price > h30: signals.append("突破30日高点"); score += 30
            if score < 40: return 0, []

        elif strategy_name == "limit_up_gene":
            pct = close.pct_change() * 100
            today_pct = pct.iloc[i]
            if today_pct >= 9.5: signals.append(f"今日涨停({today_pct:.1f}%)"); score += 40
            limit_days = sum(1 for j in range(max(0, i-30), i+1) if pct.iloc[j] >= 9.5)
            if limit_days >= 1: signals.append(f"近30日涨停{limit_days}次"); score += min(limit_days*10, 20)
            if score < 30: return 0, []

        else:
            return 0, []

        return score, signals

    except Exception as e:
        logger.debug(f"策略检查失败 [{strategy_name}]: {e}")
        return 0, []


# ─── 回测引擎 ─────────────────────────────────────────────────────────────────

class BacktestEngine:
    """
    历史回测引擎

    特点：
    - 直接从本地 CSV 缓存读取，无网络延迟
    - 支持所有策略（含缠论）
    - 每日 Top-N 选股，可选是否过滤卖出信号
    - 并发处理，速度快
    """

    def __init__(self, start_date: Optional[str] = None, end_date: Optional[str] = None,
                 weeks: Optional[int] = None):
        if end_date is None:
            end_date = datetime.now().strftime("%Y%m%d")
        if weeks is not None:
            start_date = (datetime.now() - timedelta(weeks=weeks)).strftime("%Y%m%d")
        elif start_date is None:
            start_date = (datetime.now() - timedelta(days=365)).strftime("%Y%m%d")

        self.start_date = start_date
        self.end_date   = end_date
        self._cache: Dict[str, pd.DataFrame] = {}
        self._lock = threading.Lock()

    def _get_df(self, code: str) -> pd.DataFrame:
        with self._lock:
            if code in self._cache:
                return self._cache[code]
        df = _load_cached_df(code)
        if not df.empty:
            with self._lock:
                self._cache[code] = df
        return df

    def _get_trade_dates(self) -> List[str]:
        """从上证000001获取交易日（每周取周三）"""
        df = _load_cached_df("000001")
        if df.empty:
            # 尝试网络获取
            from stock_screener.data.fetcher import get_stock_history
            df = get_stock_history("000001", days=400)
        if df.empty:
            logger.error("无法获取交易日数据")
            return []

        df["date"] = pd.to_datetime(df["date"])
        start_dt = pd.to_datetime(self.start_date)
        end_dt   = pd.to_datetime(self.end_date)
        df = df[(df["date"] >= start_dt) & (df["date"] <= end_dt)]
        df["year_week"] = (df["date"].dt.isocalendar().year.astype(str) + "_" +
                           df["date"].dt.isocalendar().week.astype(str))
        df["weekday"] = df["date"].dt.weekday

        dates = []
        for _, grp in df.groupby("year_week"):
            wed = grp[grp["weekday"] == 2]
            if not wed.empty:
                dates.append(wed.iloc[0]["date"].strftime("%Y%m%d"))
            else:
                dates.append(grp.iloc[-1]["date"].strftime("%Y%m%d"))
        return sorted(dates)

    def _process_one(self, code: str, name: str, strategy: str, trade_date: str) -> Optional[BacktestTrade]:
        """处理单只股票"""
        df = self._get_df(code)
        if df.empty or len(df) < 60:
            return None

        df["date_str"] = df["date"].dt.strftime("%Y%m%d")
        if trade_date not in df["date_str"].values:
            return None

        idx = df[df["date_str"] == trade_date].index[0]
        if idx < 30:
            return None

        hist = df.iloc[:idx+1].copy()
        score, signals = _strategy_check(strategy, hist)
        if score < SCORE_THRESHOLD:
            return None

        close = hist["close"]
        high  = hist["high"]
        low   = hist["low"]
        vol   = hist["vol"]
        pct   = close.pct_change() * 100
        risk_flags = calc_risk_flags(close, high, low, vol, pct)
        has_risk = _check_sell_signal(risk_flags)

        # 未来收益（基于完整 df，不仅是 hist）
        full_idx = df[df["date_str"] == trade_date].index[0]
        rets, exits, dds = _calc_future_returns(df, full_idx)

        return BacktestTrade(
            buy_date=trade_date,
            code=code,
            name=name,
            strategy=strategy,
            buy_price=float(hist["close"].iloc[-1]),
            score=min(score, 100),
            signals=signals,
            has_risk=has_risk,
            returns=rets,
            exit_prices=exits,
            max_drawdowns=dds,
        )

    def run(
        self,
        strategy_names: Optional[List[str]] = None,
        top_n: int = 10,
        filter_sell: bool = True,
        max_workers: int = 20,
    ) -> Dict[str, BacktestResult]:
        """
        执行回测

        Args:
            strategy_names: 策略列表，None = 所有注册策略
            top_n: 每日每策略最多选股数
            filter_sell: 是否过滤危险风险标志
            max_workers: 并发线程数
        """
        if strategy_names is None:
            strategy_names = list(STRATEGY_REGISTRY.keys())

        trade_dates = self._get_trade_dates()
        name_map    = _load_stock_names()

        # 获取所有有缓存文件的股票代码
        csv_files = glob.glob(os.path.join(_CACHE_DIR, "*.csv"))
        all_codes = [(os.path.splitext(os.path.basename(f))[0]) for f in csv_files]
        all_codes = [(c, name_map.get(c, c)) for c in all_codes]

        logger.info(f"回测: {len(trade_dates)}个交易日 × {len(strategy_names)}个策略 × {len(all_codes)}只股票")

        results = {s: BacktestResult(strategy=s) for s in strategy_names}

        for date_idx, trade_date in enumerate(trade_dates):
            logger.info(f"进度 {date_idx+1}/{len(trade_dates)} - {trade_date}")
            for strategy in strategy_names:
                tasks = [(code, name, strategy, trade_date) for code, name in all_codes]

                all_trades = []
                with ThreadPoolExecutor(max_workers=max_workers) as pool:
                    for t in pool.map(lambda a: self._process_one(*a), tasks):
                        if t:
                            all_trades.append(t)

                if filter_sell:
                    all_trades = [t for t in all_trades if not t.has_risk]

                all_trades.sort(key=lambda t: t.score, reverse=True)
                results[strategy].trades.extend(all_trades[:top_n])

        for r in results.values():
            _calc_stats(r)

        return results


def _calc_stats(result: BacktestResult):
    """计算回测统计指标"""
    trades = result.trades
    result.total_trades = len(trades)
    if not trades:
        return

    for period in HOLD_PERIODS:
        rets = [t.returns[period] for t in trades if period in t.returns]
        dds  = [t.max_drawdowns[period] for t in trades if period in t.max_drawdowns]
        if not rets:
            continue
        wins = sum(1 for r in rets if r > 0)
        result.period_stats[period] = PeriodStats(
            period=period,
            total=len(rets),
            wins=wins,
            avg_return=float(np.mean(rets)),
            avg_drawdown=float(np.mean(dds)) if dds else 0.0,
            win_rate=wins / len(rets) if rets else 0.0,
        )


def print_report(results: Dict[str, BacktestResult]):
    """打印回测报告"""
    print("\n" + "="*100)
    print(" " * 30 + "📊 歌者策略回测报告")
    print("="*100)
    print(f"\n{'策略':<18} {'交易数':>7}", end="")
    for p in HOLD_PERIODS:
        print(f"  {p}日胜率  {p}日均收益", end="")
    print()
    print("-"*100)

    for name, r in sorted(results.items(), key=lambda x: x[1].period_stats.get(10, PeriodStats(10)).win_rate, reverse=True):
        meta = STRATEGY_REGISTRY.get(name, {})
        label = meta.get("name", name)
        print(f"{label:<18} {r.total_trades:>7}", end="")
        for p in HOLD_PERIODS:
            st = r.period_stats.get(p)
            if st:
                print(f"  {st.win_rate*100:>6.1f}%  {st.avg_return:>+8.2f}%", end="")
            else:
                print(f"  {'N/A':>7}  {'N/A':>9}", end="")
        print()

    print("="*100)


def save_results(results: Dict[str, BacktestResult], output_dir: str = None):
    """保存回测结果到 JSON 文件"""
    if output_dir is None:
        output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
    os.makedirs(output_dir, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    data = {}
    for name, r in results.items():
        data[name] = {
            "total_trades": r.total_trades,
            "period_stats": {
                str(p): {"win_rate": st.win_rate, "avg_return": st.avg_return,
                         "avg_drawdown": st.avg_drawdown, "total": st.total}
                for p, st in r.period_stats.items()
            }
        }
    path = os.path.join(output_dir, f"backtest_{ts}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    logger.info(f"结果已保存: {path}")
    return path


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    engine = BacktestEngine(weeks=12)
    results = engine.run(top_n=10, filter_sell=True)
    print_report(results)
    save_results(results)
