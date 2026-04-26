"""
主入口 - 股票自动筛选工具
"""

import argparse
import logging
import sys
import os

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from stock_screener.core.engine import ScreenEngine
from stock_screener.strategies.registry import list_strategies
from stock_screener.data.fetcher import get_latest_trade_date


def main():
    parser = argparse.ArgumentParser(description="歌者 — 智能选股系统")
    subparsers = parser.add_subparsers(dest="command")

    # web 服务器模式
    web_parser = subparsers.add_parser("web", help="启动 Web 服务")
    web_parser.add_argument("--host", default="0.0.0.0")
    web_parser.add_argument("--port", type=int, default=5188)
    web_parser.add_argument("--debug", action="store_true")

    # 命令行筛选模式
    screen_parser = subparsers.add_parser("screen", help="命令行筛选")
    screen_parser.add_argument("--strategy", "-s", nargs="+",
                               help="策略名称（多个用空格分隔），不填则运行所有策略")
    screen_parser.add_argument("--market", "-m", default="主板",
                               choices=["主板", "创业板", "科创板", "all"])
    screen_parser.add_argument("--top", "-n", type=int, default=10)
    screen_parser.add_argument("--json", action="store_true", help="输出 JSON")

    # 列出策略
    subparsers.add_parser("list", help="列出所有可用策略")

    args = parser.parse_args()

    if args.command == "web":
        _start_web(args)

    elif args.command == "screen":
        _run_screen(args)

    elif args.command == "list":
        _list_strategies()

    else:
        # 默认启动 Web
        parser.print_help()
        print("\n💡 快速启动: python main.py web")


def _start_web(args):
    # 延迟 import，避免不必要的依赖
    from stock_screener.core.server import run_server
    print(f"""
╔═══════════════════════════════════════════════╗
║       🎯 歌者  智能选股系统                  ║
╠═══════════════════════════════════════════════╣
║  访问地址: http://127.0.0.1:{args.port}          ║
║  凝视资本市场的暗流                         ║
╚═══════════════════════════════════════════════╝
    """)
    run_server(host=args.host, port=args.port, debug=args.debug)


def _run_screen(args):
    import json
    logging.basicConfig(level=logging.INFO)

    engine = ScreenEngine(market=args.market, top_n=args.top)
    strategies = args.strategy or None

    print(f"📊 开始筛选... 市场: {args.market}, 策略: {strategies or '全部'}")
    result = engine.get_recommendation(strategies=strategies, top_n=args.top)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    # 打印综合推荐
    picks = result.get("comprehensive_picks", [])
    print(f"\n{'='*60}")
    print(f"  🏆 综合推荐 TOP {len(picks)}  |  交易日: {result.get('trade_date')}")
    print(f"{'='*60}")

    for i, p in enumerate(picks, 1):
        pct = p.get("pct_chg", 0)
        pct_str = f"+{pct:.2f}%" if pct >= 0 else f"{pct:.2f}%"
        wr = p.get("final_win_rate", 0)
        strategies_hit = [s["name"] for s in p.get("strategies_hit", [])]
        print(f"\n{i}. {p['name']} ({p['ts_code']})")
        print(f"   价格: {p['latest_price']}  涨幅: {pct_str}  量比: {p['volume_ratio']:.1f}x")
        print(f"   胜率: {wr*100:.1f}%  |  命中策略: {', '.join(strategies_hit)}")
        signals = p.get("all_signals", [])[:5]
        print(f"   信号: {' | '.join(signals)}")

    # 各策略独立结果
    print(f"\n{'─'*60}")
    for name, detail in result.get("strategy_details", {}).items():
        print(f"\n📌 {detail['strategy_desc']}")
        print(f"   扫描: {detail['total_scanned']} 只  命中: {detail['hit_count']} 只")
        for s in detail.get("top_stocks", [])[:3]:
            wr_str = s.get("win_rate_pct", "")
            sigs = " | ".join(s.get("signals", [])[:3])
            print(f"   → {s['name']}({s['ts_code']})  胜率:{wr_str}  [{sigs}]")


def _list_strategies():
    strategies = list_strategies()
    print(f"\n{'='*50}")
    print("  可用选股策略")
    print(f"{'='*50}")
    for s in strategies:
        tags = " | ".join(s["tags"])
        print(f"\n{s['icon']} {s['name']}  [{tags}]")
        print(f"   ID: {s['id']}")
        print(f"   {s['description']}")


if __name__ == "__main__":
    main()
