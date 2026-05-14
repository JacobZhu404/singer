"""
虚拟持仓管理器
- 模拟买入/卖出
- 持仓盈亏追踪
- 历史交易记录
"""

import json
import os
import logging
import time
import pandas as pd
import numpy as np
from datetime import datetime, date
from typing import List, Dict, Optional, Any

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
PORTFOLIO_FILE = os.path.join(DATA_DIR, "portfolio.json")
os.makedirs(DATA_DIR, exist_ok=True)


class Portfolio:
    """虚拟持仓管理器"""

    def __init__(self, initial_cash: float = 1_000_000.0):
        self.initial_cash = initial_cash
        self.positions: List[Dict] = []  # 当前持仓
        self.trades: List[Dict] = []      # 历史交易
        self._load()

    # ── 持久化 ──────────────────────────────────────────────────────────────

    def _load(self):
        if os.path.exists(PORTFOLIO_FILE):
            try:
                with open(PORTFOLIO_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.initial_cash = data.get("initial_cash", 1_000_000.0)
                self.positions = data.get("positions", [])
                self.trades = data.get("trades", [])
                logger.info(f"持仓加载: {len(self.positions)} 只, {len(self.trades)} 笔交易")
            except Exception as e:
                logger.warning(f"持仓加载失败: {e}")

    def _save(self):
        try:
            with open(PORTFOLIO_FILE, "w", encoding="utf-8") as f:
                json.dump({
                    "initial_cash": self.initial_cash,
                    "positions": self.positions,
                    "trades": self.trades,
                    "updated_at": datetime.now().isoformat(),
                }, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"持仓保存失败: {e}")

    # ── 账户净值（实时）──────────────────────────────────────────────────────

    def get_account(self, price_map: Dict[str, float]) -> Dict[str, Any]:
        """
        计算账户当前状态
        price_map: {代码: 最新价}
        """
        position_value = 0.0
        position_cost = 0.0
        position_pnl = 0.0

        for pos in self.positions:
            code = str(pos["code"])
            shares = pos["shares"]
            cost = pos["cost"]          # 持仓总成本
            avg_cost = pos["avg_cost"]   # 买入均价
            price = price_map.get(code, pos.get("current_price", 0.0))
            current_value = shares * price
            pnl = current_value - cost

            pos["current_price"] = price
            pos["current_value"] = round(current_value, 2)
            pos["position_pnl"] = round(pnl, 2)
            pos["position_pnl_pct"] = round(pnl / cost * 100, 2) if cost > 0 else 0.0

            position_value += current_value
            position_cost += cost
            position_pnl += pnl

        cash = self.initial_cash - position_cost + sum(t.get("realized_pnl", 0) for t in self.trades)
        total_value = position_value + cash
        total_pnl = total_value - self.initial_cash
        total_pnl_pct = round(total_pnl / self.initial_cash * 100, 2) if self.initial_cash > 0 else 0.0

        # 计算当日涨跌
        today_pnl = 0.0
        for pos in self.positions:
            prev = pos.get("prev_close", 0)
            cur = pos.get("current_price", 0)
            if prev > 0:
                today_pnl += (cur - prev) * pos["shares"]

        return {
            "initial_cash": self.initial_cash,
            "cash": round(cash, 2),
            "position_value": round(position_value, 2),
            "position_pnl": round(position_pnl, 2),
            "total_value": round(total_value, 2),
            "total_pnl": round(total_pnl, 2),
            "total_pnl_pct": total_pnl_pct,
            "today_pnl": round(today_pnl, 2),
            "position_count": len(self.positions),
            "trade_count": len(self.trades),
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    # ── 买入 ────────────────────────────────────────────────────────────────

    def buy(self, code: str, name: str, price: float,
            shares: int, strategy: str = "") -> Dict[str, Any]:
        """
        虚拟买入
        shares: 买入股数（100的整数倍）
        """
        if price <= 0:
            return {"success": False, "msg": "价格无效"}

        # 整手处理
        shares = int(shares // 100 * 100)
        if shares <= 0:
            return {"success": False, "msg": "买入数量必须>=100股"}

        cost = shares * price

        # 检查是否已有持仓
        existing = next((p for p in self.positions if p["code"] == code), None)
        trade_record = {
            "id": len(self.trades) + 1,
            "timestamp": datetime.now().isoformat(),
            "trade_date": datetime.now().strftime("%Y-%m-%d"),
            "action": "buy",
            "code": code,
            "name": name,
            "price": price,
            "shares": shares,
            "amount": round(cost, 2),
            "strategy": strategy,
        }

        if existing:
            # 追加买入：更新均价
            old_cost = existing["shares"] * existing["avg_cost"]
            new_cost = old_cost + cost
            new_shares = existing["shares"] + shares
            existing["shares"] = new_shares
            existing["avg_cost"] = round(new_cost / new_shares, 3)
            existing["cost"] = round(new_cost, 2)
            existing["name"] = name
            existing["current_price"] = price
            trade_record["note"] = "追加"
        else:
            self.positions.append({
                "code": code,
                "name": name,
                "shares": shares,
                "avg_cost": round(price, 3),
                "cost": round(cost, 2),
                "current_price": price,
                "current_value": round(cost, 2),
                "position_pnl": 0.0,
                "position_pnl_pct": 0.0,
                "buy_date": datetime.now().strftime("%Y-%m-%d"),
                "buy_strategy": strategy,
                "prev_close": price,  # 初始化为当前价
            })

        self.trades.append(trade_record)
        self._save()

        logger.info(f"买入: {code} {name} {shares}股@{price}元, 共计{cost:.2f}元")
        return {
            "success": True,
            "msg": f"买入成功: {name}({code}) {shares}股 × {price}元 = {cost:.2f}元",
            "trade": trade_record,
        }

    # ── 卖出 ────────────────────────────────────────────────────────────────

    def sell(self, code: str, price: float, shares: int = None, note: str = "") -> Dict[str, Any]:
        """虚拟卖出（减仓/清仓）"""
        if price <= 0:
            return {"success": False, "msg": "价格无效"}

        existing = next((p for p in self.positions if p["code"] == code), None)
        if not existing:
            return {"success": False, "msg": "该股票不在持仓中"}

        if shares is None:
            shares = existing["shares"]  # 全部卖出

        shares = int(shares // 100 * 100)
        if shares <= 0 or shares > existing["shares"]:
            return {"success": False, "msg": "卖出数量无效"}

        sell_amount = shares * price
        realized_pnl = (price - existing["avg_cost"]) * shares
        is_clear = shares == existing["shares"]
        action_label = note or ("清仓" if is_clear else "减仓")

        trade_record = {
            "id": len(self.trades) + 1,
            "timestamp": datetime.now().isoformat(),
            "trade_date": datetime.now().strftime("%Y-%m-%d"),
            "action": "sell",
            "code": code,
            "name": existing["name"],
            "price": price,
            "shares": shares,
            "amount": round(sell_amount, 2),
            "avg_cost": existing["avg_cost"],
            "realized_pnl": round(realized_pnl, 2),
            "hold_days": (datetime.now().date() - datetime.strptime(
                existing["buy_date"], "%Y-%m-%d").date()).days,
            "note": action_label,
        }

        if is_clear:
            self.positions = [p for p in self.positions if p["code"] != code]
        else:
            existing["shares"] -= shares
            existing["cost"] = round(existing["avg_cost"] * existing["shares"], 2)
            existing["current_value"] = round(existing["shares"] * price, 2)

        self.trades.append(trade_record)
        self._save()

        logger.info(f"{action_label}: {code} {existing['name']} {shares}股@{price}元, "
                    f"盈亏{realized_pnl:.2f}元({realized_pnl/(existing['avg_cost']*shares)*100:.1f}%)")
        return {
            "success": True,
            "msg": f"{action_label}成功: {existing['name']}({code}) {shares}股@{price}元, "
                   f"已实现盈亏 {realized_pnl:.2f}元 ({realized_pnl/(existing['avg_cost']*shares)*100:.1f}%)",
            "trade": trade_record,
        }

    # ── 批量更新持仓价格（每日收盘后调用）───────────────────────────────────

    def update_prices(self, price_map: Dict[str, float]):
        """更新所有持仓的当前价（用于收盘后刷新盈亏）"""
        for pos in self.positions:
            code = pos["code"]
            price = price_map.get(code, pos.get("current_price", 0))
            if pos.get("prev_close", 0) == 0:
                pos["prev_close"] = price  # 初始化昨日收盘
            pos["current_price"] = price
            pos["current_value"] = round(price * pos["shares"], 2)
            pos["position_pnl"] = round((price - pos["avg_cost"]) * pos["shares"], 2)
            pos["position_pnl_pct"] = round((price - pos["avg_cost"]) / pos["avg_cost"] * 100, 2)
        self._save()

    # ── 统计 ────────────────────────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        """交易统计"""
        if not self.trades:
            return {
                "total_trades": 0,
                "winning_trades": 0,
                "losing_trades": 0,
                "win_rate": 0.0,
                "avg_win": 0.0,
                "avg_loss": 0.0,
                "total_realized_pnl": 0.0,
                "max_win": 0.0,
                "max_loss": 0.0,
            }

        sells = [t for t in self.trades if t["action"] == "sell" and t.get("realized_pnl") is not None]
        if not sells:
            return {"total_trades": 0, "winning_trades": 0, "losing_trades": 0,
                    "win_rate": 0.0, "avg_win": 0.0, "avg_loss": 0.0,
                    "total_realized_pnl": 0.0, "max_win": 0.0, "max_loss": 0.0}

        pnls = [t["realized_pnl"] for t in sells]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]

        return {
            "total_trades": len(sells),
            "winning_trades": len(wins),
            "losing_trades": len(losses),
            "win_rate": round(len(wins) / len(sells) * 100, 1),
            "avg_win": round(sum(wins) / len(wins), 2) if wins else 0.0,
            "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0.0,
            "total_realized_pnl": round(sum(pnls), 2),
            "max_win": round(max(pnls), 2) if pnls else 0.0,
            "max_loss": round(min(pnls), 2) if pnls else 0.0,
            "profit_factor": round(abs(sum(wins) / sum(losses)), 2) if losses and sum(losses) != 0 else 0.0,
        }

    def get_trade_history(self, limit: int = 50) -> List[Dict]:
        """获取交易历史（最近limit条）"""
        return sorted(self.trades, key=lambda t: t["id"], reverse=True)[:limit]


# ── 全局单例 ─────────────────────────────────────────────────────────────────
_portfolio: Optional[Portfolio] = None


def get_portfolio() -> Portfolio:
    global _portfolio
    if _portfolio is None:
        _portfolio = Portfolio()
    return _portfolio
