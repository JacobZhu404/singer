"""
Flask Web 服务器 - 股票筛选工具后端
提供 REST API + 前端页面
"""

import json
import logging
import os
import threading
import queue
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Optional, Dict, List

from flask import Flask, jsonify, request, render_template_string, send_from_directory, Response
from flask_cors import CORS

import pandas as pd

from .engine import ScreenEngine
from ..strategies.registry import list_strategies
from ..data.fetcher import get_latest_trade_date, get_stock_realtime, get_stock_history, market_scanner
from ..portfolio.manager import get_portfolio
from ..portfolio.sell_analyzer import get_analyzer
from ..strategies.base import ScreenResult

# ── 日志配置 ──────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

# ── Flask App ──────────────────────────────────────────────────────────────
# 指定静态文件目录，禁用内置静态路由避免路径冲突
_WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "web")
_WEB_DIR = os.path.normpath(_WEB_DIR)
app = Flask(__name__, static_folder=None)  # 禁用内置静态路由
CORS(app)

# 全局引擎（单例）
_engine: Optional[ScreenEngine] = None
_engine_lock = threading.Lock()
_last_result: Optional[dict] = None
_is_running = False
_stop_event = threading.Event()
_screen_progress: dict = {
    "phase": "idle",
    "current": "",
    "current_index": 0,
    "total": 0,
    "pct": 0,
    "strategies": {},
}
_progress_lock = threading.Lock()

# ── 流式结果（并行策略执行时，每个策略完成后即存入）──
_partial_results: Dict[str, dict] = {}
_partial_result_lock = threading.Lock()
_current_strategies_run: List[str] = []
_current_strategies_lock = threading.Lock()

# ── SSE 事件队列 ──
_sse_queues: List[queue.Queue] = []
_sse_queues_lock = threading.Lock()

# 各阶段对应的进度条百分比范围 (low, high)
_PHASE_RANGES = {
    "prefetch": (0, 25),
    "precalc": (25, 40),
    "running": (40, 100),
    "merging": (99, 99),
    "download_done": (100, 100),
    "done": (100, 100),
}

# ── 筛选结果持久化 ──
_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
_DATA_DIR = os.path.normpath(_DATA_DIR)
os.makedirs(_DATA_DIR, exist_ok=True)
_RESULT_FILE = os.path.join(_DATA_DIR, "last_screen_result.json")

_enable_realtime = True


def _load_last_result() -> Optional[dict]:
    """启动时从文件恢复上次筛选结果"""
    if os.path.exists(_RESULT_FILE):
        try:
            with open(_RESULT_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"加载上次筛选结果失败: {e}")
    return None


def _save_last_result(result: dict):
    """筛选完成后保存结果到文件"""
    try:
        with open(_RESULT_FILE, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"保存筛选结果失败: {e}")


# 启动时尝试恢复
_last_result = _load_last_result()
if _last_result:
    logger.info("已恢复上次筛选结果")


def _broadcast_sse(event: str, data: dict):
    """向所有 SSE 连接广播事件"""
    payload = json.dumps(data, ensure_ascii=False)
    msg = f"event: {event}\ndata: {payload}\n\n"
    with _sse_queues_lock:
        dead = []
        for q in _sse_queues:
            try:
                q.put(msg, block=False)
            except Exception:
                dead.append(q)
        for q in dead:
            if q in _sse_queues:
                _sse_queues.remove(q)


def get_engine(market: str = "主板") -> ScreenEngine:
    global _engine
    with _engine_lock:
        if _engine is None or _engine.market != market:
            _engine = ScreenEngine(market=market, top_n=20)
    return _engine


# ── API 路由 ───────────────────────────────────────────────────────────────
@app.route("/api/strategies", methods=["GET"])
def api_strategies():
    """获取所有可用策略列表"""
    return jsonify({"code": 0, "data": list_strategies()})


@app.route("/api/screen", methods=["POST"])
def api_screen():
    """
    执行策略筛选
    Body: { strategies: ["macd_bull", ...], market: "主板", top_n: 20, force_refresh: false }
    """
    global _last_result, _is_running

    if _is_running:
        return jsonify({"code": 1, "msg": "筛选任务正在进行中，请稍候..."})

    body = request.get_json() or {}
    strategies = body.get("strategies", None)
    market = body.get("market", "主板")
    top_n = int(body.get("top_n", 20))
    force_refresh = bool(body.get("force_refresh", False))
    skip_download = bool(body.get("skip_download", False))

    def run_task():
        global _last_result, _is_running, _partial_results, _current_strategies_run

        def _on_progress(phase: str, current: str, idx: int, total: int):
            with _progress_lock:
                _screen_progress["phase"] = phase
                _screen_progress["current"] = current
                _screen_progress["current_index"] = idx
                _screen_progress["total"] = total
                low, high = _PHASE_RANGES.get(phase, (0, 100))
                if phase == "running":
                    engine_pct = engine.get_progress().get("pct", 0)
                    pct = low + int(engine_pct / 100 * (high - low))
                    pct = min(pct, 99)
                elif phase == "done":
                    pct = 100
                else:
                    if total > 0:
                        phase_pct = int(idx / total * 100)
                    else:
                        phase_pct = 0
                    pct = low + int(phase_pct / 100 * (high - low))
                    # merging 阶段允许到达100%，其他阶段最高99%（done阶段单独处理）
                    if phase != "merging":
                        pct = min(pct, 99)
                _screen_progress["pct"] = pct
                # 同步引擎内部的策略子进度
                _screen_progress["strategies"] = dict(engine.get_progress().get("strategies", {}))

        try:
            _is_running = True
            _stop_event.clear()
            # 重置进度和部分结果
            with _progress_lock:
                _screen_progress["phase"] = "prefetch"
                _screen_progress["current"] = "准备加载行情数据..."
                _screen_progress["current_index"] = 0
                _screen_progress["total"] = 0
                _screen_progress["pct"] = 0
                _screen_progress["strategies"] = {}
            with _partial_result_lock:
                _partial_results = {}
            with _current_strategies_lock:
                _current_strategies_run = strategies or []

            def _on_strategy_done(name: str, sr: ScreenResult):
                summary = _strategy_result_to_summary(name, sr)
                with _partial_result_lock:
                    _partial_results[name] = summary
                # SSE 推送：策略完成事件
                _broadcast_sse("strategy_done", {"name": name, "data": summary})

            engine = get_engine(market)
            engine.top_n = top_n
            engine._stop_event = _stop_event
            result = engine.get_recommendation(
                strategies=strategies,
                top_n=top_n,
                progress_callback=_on_progress,
                force_refresh=force_refresh,
                on_strategy_done=_on_strategy_done,
                skip_download=skip_download,
            )
            _last_result = result
        except KeyboardInterrupt:
            logger.info("筛选任务被用户中断")
            _last_result = {"error": "用户中断了筛选"}
        except Exception as e:
            logger.error(f"筛选失败: {e}")
            _last_result = {"error": str(e)}
        finally:
            _is_running = False
            _stop_event.clear()
            with _progress_lock:
                _screen_progress["phase"] = "done"
                _screen_progress["current"] = "筛选完成"
            # 保存结果到本地，刷新页面后可恢复
            if _last_result is not None:
                _save_last_result(_last_result)

    t = threading.Thread(target=run_task, daemon=True)
    t.start()

    return jsonify({"code": 0, "msg": "筛选任务已启动，请轮询 /api/result 获取结果"})


@app.route("/api/screen/progress", methods=["GET"])
def api_screen_progress():
    """获取当前筛选进度"""
    with _progress_lock:
        progress = dict(_screen_progress)
    # 实时同步引擎的策略子进度（更准确，避免过期）
    try:
        from ..core.engine import get_engine as _get_eng
        engine = _get_eng()
        if engine:
            eng_progress = engine.get_progress()
            if eng_progress.get("strategies"):
                progress["strategies"] = dict(eng_progress["strategies"])
    except Exception:
        pass
    return jsonify({"code": 0, "data": progress})


@app.route("/api/screen/stop", methods=["POST"])
def api_screen_stop():
    """停止正在进行的筛选任务"""
    if not _is_running:
        return jsonify({"code": 1, "msg": "当前没有正在运行的筛选任务"})
    _stop_event.set()
    return jsonify({"code": 0, "msg": "停止信号已发送，请稍候..."})


@app.route("/api/data_status", methods=["GET"])
def api_data_status():
    """
    查询当前K线缓存状态。
    返回已缓存的股票数量，辅助用户判断是否需要先更新数据。
    """
    from ..data.fetcher import market_scanner, get_stock_list
    status = market_scanner.get_cache_status()
    # 使用内存缓存数量（与当前进度条保持一致）
    cached = status.get("memory_cached", 0)
    # 全市场股票总数（用于前端显示 "3257 / 3400"）
    try:
        total_stocks = len(get_stock_list())
    except Exception:
        total_stocks = cached  # 获取失败时以缓存数代替
    # 判断数据是否足够（覆盖全市场50%以上认为数据充足）
    data_ready = (total_stocks > 0 and cached >= total_stocks * 0.5) or cached >= 500
    if data_ready:
        hint = f"已缓存 {cached} 只 / 全市场 {total_stocks} 只 ✅"
    elif cached > 0:
        hint = f"已缓存 {cached} 只 / 全市场 {total_stocks} 只 ⚠️ 建议更新"
    else:
        hint = "尚未下载数据，请先更新"
    return jsonify({
        "code": 0,
        "data": {
            **status,
            "cached_count": cached,
            "total_stocks": total_stocks,
            "data_ready": data_ready,
            "hint": hint,
        }
    })


@app.route("/api/download", methods=["POST"])
def api_download():
    """
    单独下载/更新K线数据（不跑策略）。
    Body: { "force_refresh": false }
    前端轮询 /api/screen/progress 查看进度（phase=prefetch）。
    """
    body = request.get_json() or {}
    force = bool(body.get("force_refresh", False))

    if _is_running:
        return jsonify({"code": 2, "msg": "当前有任务正在运行，请稍后再试"})

    def download_task():
        global _is_running
        try:
            _is_running = True
            _stop_event.clear()
            with _progress_lock:
                _screen_progress["phase"] = "prefetch"
                _screen_progress["current"] = "准备加载行情数据..."
                _screen_progress["current_index"] = 0
                _screen_progress["total"] = 0
                _screen_progress["pct"] = 0
                _screen_progress["strategies"] = {}

            def _on_progress(phase, current, idx, total):
                with _progress_lock:
                    _screen_progress["phase"] = phase
                    _screen_progress["current"] = current
                    _screen_progress["current_index"] = idx
                    _screen_progress["total"] = total
                    low, high = _PHASE_RANGES.get(phase, (0, 100))
                    if total > 0:
                        phase_pct = int(idx / total * 100)
                    else:
                        phase_pct = 0
                    pct = low + int(phase_pct / 100 * (high - low))
                    pct = min(pct, 99)
                    _screen_progress["pct"] = pct

            engine = get_engine("主板")
            engine._stop_event = _stop_event
            engine.download_data(force_refresh=force, progress_callback=_on_progress)
        except Exception as e:
            logger.error(f"数据下载失败: {e}")
            with _progress_lock:
                _screen_progress["phase"] = "download_done"
                _screen_progress["current"] = f"下载失败: {e}"
        finally:
            _is_running = False
            with _progress_lock:
                if _screen_progress["phase"] != "download_done":
                    _screen_progress["phase"] = "download_done"
                    _screen_progress["current"] = "数据更新完成"
                    _screen_progress["pct"] = 100

    t = threading.Thread(target=download_task, daemon=True)
    t.start()
    return jsonify({"code": 0, "msg": "数据下载任务已启动，请轮询 /api/screen/progress 查看进度"})


@app.route("/api/result", methods=["GET"])
def api_result():
    """获取最新筛选结果（运行中返回已完成的策略部分结果）"""
    if _is_running:
        with _partial_result_lock:
            partial = dict(_partial_results)
        with _current_strategies_lock:
            strategies_run = list(_current_strategies_run)
        return jsonify({
            "code": 2,
            "msg": "筛选中...",
            "data": {
                "strategies_run": strategies_run,
                "strategy_details": partial,
                "comprehensive_picks": [],
            }
        })
    if _last_result is None:
        return jsonify({"code": 1, "msg": "尚无筛选结果，请先调用 /api/screen", "data": None})
    return jsonify({"code": 0, "msg": "ok", "data": _last_result})


@app.route("/api/status", methods=["GET"])
def api_status():
    """获取系统状态（含当前进度，便于前端刷新后恢复）"""
    with _progress_lock:
        progress = dict(_screen_progress)
    with _partial_result_lock:
        partial_count = len(_partial_results)
    return jsonify({
        "code": 0,
        "data": {
            "is_running": _is_running,
            "has_result": _last_result is not None,
            "trade_date": get_latest_trade_date(),
            "server_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "progress": progress,
            "partial_count": partial_count,
        }
    })


@app.route("/api/screen/events")
def api_screen_events():
    """SSE 端点：实时推送策略完成事件"""
    def event_stream():
        q = queue.Queue(maxsize=100)
        with _sse_queues_lock:
            _sse_queues.append(q)
        # 发送连接成功事件
        q.put("event: connected\ndata: {}\n\n")
        try:
            while True:
                msg = q.get(timeout=30)
                yield msg
        except Exception:
            pass
        finally:
            with _sse_queues_lock:
                if q in _sse_queues:
                    _sse_queues.remove(q)

    return Response(event_stream(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/api/quick_screen", methods=["POST"])
def api_quick_screen():
    """
    同步快速筛选（单策略，适合快速测试）
    Body: { strategy: "macd_bull", market: "主板", top_n: 10 }
    """
    body = request.get_json() or {}
    strategy_name = body.get("strategy", "macd_bull")
    market = body.get("market", "主板")
    top_n = int(body.get("top_n", 20))

    try:
        engine = get_engine(market)
        engine.top_n = top_n
        result = engine.run_single(strategy_name)
        return jsonify({
            "code": 0,
            "data": {
                "strategy_name": result.strategy_name,
                "strategy_desc": result.strategy_desc,
                "trade_date": result.trade_date,
                "total_scanned": result.total_scanned,
                "hit_count": len(result.signals),
                "stocks": [
                    {
                        "ts_code": s.ts_code,
                        "name": s.name,
                        "score": s.score,
                        "signals": s.signals,
                        "latest_price": s.latest_price,
                        "pct_chg": s.pct_chg,
                        "volume_ratio": s.volume_ratio,
                        "extra": s.extra,
                    }
                    for s in result.signals
                ]
            }
        })
    except Exception as e:
        logger.error(f"快速筛选失败: {e}")
        return jsonify({"code": 1, "msg": str(e)})


# ════════════════════════════════════════════════════════════════════════
#  持仓 API
# ════════════════════════════════════════════════════════════════════════

@app.route("/api/portfolio", methods=["GET"])
def api_portfolio():
    """获取持仓账户概览"""
    try:
        pf = get_portfolio()
        # 并发拉取持仓股票的最新价（替代串行 sleep）
        price_map = {}
        if pf.positions:
            def _fetch_price(pos):
                code = pos["code"]
                try:
                    quote = get_stock_realtime(code)
                    price = quote.get("最新价", 0)
                    if price > 0:
                        return code, price
                except Exception:
                    pass
                return code, pos.get("current_price", 0)

            with ThreadPoolExecutor(max_workers=min(10, len(pf.positions))) as pool:
                for code, price in pool.map(_fetch_price, pf.positions):
                    price_map[code] = price

        account = pf.get_account(price_map)
        stats = pf.get_stats()
        return jsonify({"code": 0, "data": {
            "account": account,
            "positions": pf.positions,
            "stats": stats,
        }})
    except Exception as e:
        logger.error(f"获取持仓失败: {e}")
        return jsonify({"code": 1, "msg": str(e)})


@app.route("/api/quick_price", methods=["GET"])
def api_quick_price():
    """快速获取单只股票实时最新价，用于卖出确认框展示"""
    code = request.args.get("code", "")
    if not code:
        return jsonify({"code": 1, "msg": "股票代码不能为空"})
    quote = get_stock_realtime(code)
    price = quote.get("最新价", 0)
    name = quote.get("名称", code)
    return jsonify({"code": 0, "data": {"code": code, "name": name, "price": price}})


@app.route("/api/portfolio/buy", methods=["POST"])
def api_buy():
    """虚拟买入
    Body: { code, name, price, shares, strategy }
    """
    body = request.get_json() or {}
    code = body.get("code", "")
    name = body.get("name", code)
    price = float(body.get("price", 0))
    shares = int(body.get("shares", 100))
    strategy = body.get("strategy", "")

    if not code:
        return jsonify({"code": 1, "msg": "股票代码不能为空"})

    # 如果未提供价格，自动获取实时最新价（收盘后即为收盘价）
    if price <= 0:
        quote = get_stock_realtime(code)
        price = quote.get("最新价", 0)
        if price <= 0:
            return jsonify({"code": 1, "msg": "无法获取当前价格，请稍后重试"})

    pf = get_portfolio()
    result = pf.buy(code, name, price, shares, strategy)
    return jsonify({"code": 0 if result["success"] else 1, "data": result})


@app.route("/api/portfolio/sell", methods=["POST"])
def api_sell():
    """虚拟卖出（减仓/清仓）
    Body: { code, price, shares, note }
    """
    body = request.get_json() or {}
    code = body.get("code", "")
    price = float(body.get("price", 0))
    shares = body.get("shares")
    note = body.get("note", "")

    if not code:
        return jsonify({"code": 1, "msg": "股票代码不能为空"})

    # 如果未提供价格，自动获取实时最新价（收盘后即为收盘价）
    if price <= 0:
        quote = get_stock_realtime(code)
        price = quote.get("最新价", 0)
        if price <= 0:
            return jsonify({"code": 1, "msg": "无法获取当前价格，请稍后重试"})

    pf = get_portfolio()
    result = pf.sell(code, price, shares, note)
    return jsonify({"code": 0 if result["success"] else 1, "data": result})


@app.route("/api/portfolio/trades", methods=["GET"])
def api_trades():
    """获取交易历史"""
    pf = get_portfolio()
    trades = pf.get_trade_history(limit=50)
    return jsonify({"code": 0, "data": trades})


@app.route("/api/portfolio/signals", methods=["GET"])
def api_portfolio_signals():
    """
    卖出信号分析
    对所有持仓股票进行多维度卖出信号检测
    """
    try:
        from ..data.fetcher import market_scanner
        pf = get_portfolio()
        analyzer = get_analyzer()

        if not pf.positions:
            return jsonify({"code": 0, "data": {
                "signals": [],
                "urgent_count": 0,
                "sell_count": 0,
                "reduce_count": 0,
                "total_positions": 0,
            }})

        results = []
        urgent_count = 0
        sell_count = 0
        reduce_count = 0

        for pos in pf.positions:
            code = pos["code"]
            # 优先使用实时最新价，保证盈亏计算和技术指标一致性
            quote = get_stock_realtime(code)
            price = quote.get("最新价", 0)
            if price <= 0:
                price = pos.get("current_price", 0)
            if price <= 0:
                continue

            # 拉取历史K线（60日），使用 MarketScanner 以支持盘中实时数据
            try:
                kl = market_scanner.get_history(code, days=60)
            except Exception:
                kl = pd.DataFrame()

            sig = analyzer.analyze_position(
                code=code,
                name=pos["name"],
                avg_cost=pos["avg_cost"],
                current_price=price,
                buy_date=pos.get("buy_date", ""),
                history=kl,
            )

            results.append({
                "code": sig.code,
                "name": sig.name,
                "current_price": round(sig.current_price, 2),
                "avg_cost": round(sig.avg_cost, 3),
                "pnl_pct": sig.pnl_pct,
                "hold_days": sig.hold_days,
                "signals": sig.signals,
                "urgent_signals": sig.urgent_signals,
                "sell_score": sig.sell_score,
                "sell_level": sig.sell_level,
                "sell_level_label": sig.sell_level_label,
                "sell_level_icon": sig.sell_level_icon,
                "sell_level_color": sig.sell_level_color,
                "action": sig.action,
                "reason": sig.reason,
                "rsi14": sig.rsi14,
                "macd_state": sig.macd_state,
                "bollinger_pos": sig.bollinger_pos,
                "trend": sig.trend,
                "risk_tag": sig.risk_tag,
                "risk_score": sig.risk_score,
                "risk_flags": sig.risk_flags,
            })

            if sig.sell_level == "URGENT":
                urgent_count += 1
            elif sig.sell_level == "SELL":
                sell_count += 1
            elif sig.sell_level == "REDUCE":
                reduce_count += 1

        # 按卖出紧迫度排序（最高在前）
        results.sort(key=lambda x: x["sell_score"], reverse=True)

        return jsonify({"code": 0, "data": {
            "signals": results,
            "urgent_count": urgent_count,
            "sell_count": sell_count,
            "reduce_count": reduce_count,
            "total_positions": len(pf.positions),
        }})
    except Exception as e:
        logger.error(f"卖出信号分析失败: {e}")
        return jsonify({"code": 1, "msg": str(e)})


@app.route("/api/config/realtime", methods=["GET"])
def api_get_realtime_config():
    return jsonify({"code": 0, "data": {"enabled": _enable_realtime}})


@app.route("/api/config/realtime", methods=["POST"])
def api_set_realtime_config():
    global _enable_realtime
    body = request.get_json() or {}
    _enable_realtime = bool(body.get("enabled", True))
    # 同步到全局扫描器
    from ..data.fetcher import market_scanner
    market_scanner._include_realtime = _enable_realtime
    return jsonify({"code": 0, "data": {"enabled": _enable_realtime}})


@app.route("/static/<path:filename>", methods=["GET"])
def serve_static(filename):
    """显式静态文件路由，兼容 Flask 非 root 模块启动场景"""
    return send_from_directory(os.path.join(_WEB_DIR, "static"), filename)


@app.route("/", methods=["GET"])
def index():
    """返回前端页面"""
    return render_template_string(_load_template())


# ── 前端 HTML（惰性加载，启动时不读取文件）──
def _load_template() -> str:
    """惰性加载 HTML 模板，首次访问时读取"""
    _path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "web", "templates", "index.html"
    )
    _path = os.path.normpath(_path)
    try:
        with open(_path, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        logger.error(f"HTML模板文件不存在: {_path}")
        return "<html><body><h1>模板文件缺失，请检查部署</h1></body></html>"


def _strategy_result_to_summary(name: str, sr) -> dict:
    """将策略结果转换为摘要字典（供流式回调用）"""
    return {
        "strategy_name": sr.strategy_name,
        "strategy_desc": sr.strategy_desc,
        "trade_date": sr.trade_date,
        "total_scanned": sr.total_scanned,
        "hit_count": len(sr.all_signals),
        "stocks": [
            {
                "ts_code": s.ts_code,
                "name": s.name,
                "score": s.score,
                "signals": s.signals,
                "latest_price": s.latest_price,
                "pct_chg": s.pct_chg,
                "volume_ratio": s.volume_ratio,
                "extra": s.extra,
            }
            for s in sr.signals
        ],
    }


def run_server(host: str = "0.0.0.0", port: int = 5188, debug: bool = False):
    logger.info(f"启动股票筛选工具服务: http://{host}:{port}")
    app.run(host=host, port=port, debug=debug, use_reloader=False)
    app.run(host=host, port=port, debug=debug, use_reloader=False)
