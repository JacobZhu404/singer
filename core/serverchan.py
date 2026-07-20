"""
Server 酱 (ServerChan) 微信推送模块

功能：将选股结果推送到微信（通过 Server 酱 API）
文档：https://sct.ftqq.com/

用法：
    from core.serverchan import ServerChanPusher

    pusher = ServerChanPusher(send_key="your_send_key")
    pusher.push_screen_result(result_dict, top_n=20)
"""

import json
import requests
from typing import Dict, List, Any, Optional


class ServerChanPusher:
    """Server 酱微信推送器"""

    API_URL = "https://sctapi.ftqq.com/{send_key}.send"

    def __init__(self, send_key: str):
        if not send_key or not send_key.startswith("SCT"):
            raise ValueError("SendKey 格式错误，应以 SCT 开头")
        self.send_key = send_key
        self.api_url = self.API_URL.format(send_key=send_key)

    def _send(self, title: str, desp: str) -> Dict[str, Any]:
        """调用 Server 酱 API 发送消息"""
        try:
            resp = requests.post(
                self.api_url,
                json={"title": title, "desp": desp},
                timeout=15,
                headers={"Content-Type": "application/json"},
            )
            data = resp.json()
            if data.get("code") != 0:
                return {"success": False, "error": data.get("message", "未知错误")}
            return {"success": True, "data": data.get("data", {})}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def push_screen_result(
        self,
        result: Dict[str, Any],
        top_n: int = 20,
        market_label: str = "全部板块",
    ) -> Dict[str, Any]:
        """
        推送选股结果到微信

        Args:
            result: ScreenEngine.get_recommendation() 返回的结果字典
            top_n: 推送前 N 只股票
            market_label: 市场标签（如"主板"、"全部板块"）

        Returns:
            {"success": bool, "error": str|None}
        """
        picks = result.get("comprehensive_picks", [])[:top_n]
        trade_date = result.get("trade_date", "")

        title = f"🎯 歌者选股 | {market_label} Top{len(picks)} | {trade_date}"

        lines = [
            "## 筛选参数",
            f"市场: {market_label}",
            f"交易日: {trade_date}",
            "策略: MACD多头/强势股/神奇九转/右侧交易/RSI超卖/布林反弹/量价突破/缠论",
            "",
            "## 推荐列表",
        ]

        for i, p in enumerate(picks, 1):
            name = p.get("name", "")
            code = p.get("ts_code", "")
            score = p.get("composite_score", 0)
            price = p.get("latest_price", 0)
            pct = p.get("pct_chg", 0)
            vr = p.get("volume_ratio", 0)
            pe = p.get("pe", 0)
            pb = p.get("pb", 0)
            pct_str = f"+{pct:.2f}%" if pct >= 0 else f"{pct:.2f}%"

            lines.append(f"## {i}. {name} ({code})")
            lines.append(
                f"评分: **{score:.0f}** | 价格: {price} | 涨幅: {pct_str}"
            )
            lines.append(f"量比: {vr:.1f}x | PE: {pe:.1f} | PB: {pb:.2f}")
            lines.append("")

        return self._send(title, "\n".join(lines))

    def push_text(self, title: str, content: str) -> Dict[str, Any]:
        """推送自定义文本消息"""
        return self._send(title, content)


def push_result_from_config(result: Dict[str, Any], config_path: str = "serverchan_config.json") -> Dict[str, Any]:
    """
    从配置文件读取 SendKey 并推送结果

    Args:
        result: 选股结果字典
        config_path: Server 酱配置文件路径（JSON，格式: {"send_key": "SCTxxx"}）

    Returns:
        {"success": bool, "error": str|None}
    """
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        send_key = cfg.get("send_key", "")
        if not send_key:
            return {"success": False, "error": f"配置文件中缺少 send_key: {config_path}"}
        pusher = ServerChanPusher(send_key)
        return pusher.push_screen_result(result)
    except FileNotFoundError:
        return {"success": False, "error": f"配置文件不存在: {config_path}"}
    except Exception as e:
        return {"success": False, "error": str(e)}
