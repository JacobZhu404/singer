"""
Flask Web 服务器 - 股票筛选工具后端
提供 REST API + 前端页面
"""

import json
import logging
import os
import time
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
from ..data.fetcher import get_latest_trade_date, get_stock_realtime, get_stock_history, market_scanner, is_market_open
from ..portfolio.manager import get_portfolio
from ..portfolio.sell_analyzer import get_analyzer
from ..strategies.base import ScreenResult

# ── 持仓信号缓存 ──
_portfolio_signals_cache = {
    "data": None,
    "timestamp": None,
    "cache_ttl_minutes": 5,  # 交易时间5分钟，休市30分钟
}

# ── 日志配置 ──────────────────────────────────────────────────────
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
_task_start_time = 0.0
_task_timeout = 900.0  # 15分钟超时
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
    "prefetch_init": (0, 8),
    "prefetch_tdx": (8, 15),
    "prefetch_fetch": (15, 25),
    "prefetch_done": (25, 25),
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


def _clean_nan(obj):
    """递归清理 NaN/Inf 值，转为 None"""
    import math
    if isinstance(obj, dict):
        return {k: _clean_nan(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_clean_nan(v) for v in obj]
    elif isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    return obj


def _save_last_result(result: dict):
    """筛选完成后保存结果到文件"""
    try:
        # 清理 NaN 值
        result = _clean_nan(result)
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
    # 清理 NaN 值
    data = _clean_nan(data)
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
            # 超时检测：防止异常导致任务卡住
            if _is_running and (time.time() - _task_start_time) < _task_timeout:
                return jsonify({"code": 1, "msg": "任务正在进行中，请稍后重试"})
            _is_running = True
            _task_start_time = time.time()
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
            logger.error(f"筛选失败: {e}", exc_info=True)
            try:
                from .observability import obs
                obs.error("web.api", "run_task", f"筛选任务异常: {e}",
                          context={"action": "任务终止"}, exc=e)
            except Exception:
                logger.debug("obs.error run_task failed", exc_info=True)
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
        engine = get_engine()  # get_engine 在本文件定义
        if engine:
            eng_progress = engine.get_progress()
            if eng_progress.get("strategies"):
                progress["strategies"] = dict(eng_progress["strategies"])
    except Exception as e:
        try:
            from .observability import obs
            obs.error("web.api", "screen_progress",
                      f"读取进度失败: {e}",
                      context={"action": "返回上次快照"}, exc=e)
        except Exception:
            logger.debug("obs.error screen_progress failed", exc_info=True)
    return jsonify({"code": 0, "data": progress})


@app.route("/api/screen/stop", methods=["POST"])
def api_screen_stop():
    """
    停止正在进行的筛选任务。

    行为：
      1. 发出停止信号（_stop_event）
      2. 最多等 5s 让工作线程自然退出（看到 _is_running 变 False）
      3. 若仍未退出（多半卡在网络 I/O），强制重置状态标志，但后台线程会在
         自身网络调用结束后自然退出。返回 code=2 + forced=true 提示用户。

    返回：
      - code=0: 任务已实际停止
      - code=1: 当前无运行中任务
      - code=2: 信号已发但任务未在 5s 内响应，已强制重置状态（forced=true）
    """
    global _is_running

    try:
        from .observability import obs
    except Exception:
        obs = None

    if not _is_running:
        return jsonify({"code": 1, "msg": "当前没有正在运行的筛选任务"})

    _stop_event.set()
    stop_requested_at = time.time()
    if obs:
        obs.warn("web.api", "screen_stop", "收到用户停止请求，已置 stop_event",
                 context={"task_age_sec": round(stop_requested_at - _task_start_time, 1)})

    # 轮询等待自然退出（最多 5s）
    deadline = stop_requested_at + 5.0
    while time.time() < deadline:
        if not _is_running:
            elapsed = time.time() - stop_requested_at
            if obs:
                obs.info("web.api", "screen_stop",
                         f"任务在 {elapsed:.1f}s 内自然退出")
            return jsonify({
                "code": 0,
                "msg": f"✅ 已成功停止（耗时 {elapsed:.1f}s）",
                "stopped": True,
            })
        time.sleep(0.2)

    # 5s 仍未退出 —— 强制重置标志位，让前端可以重新发起筛选
    # 注意：后台线程会在自身阻塞的网络调用结束后走到 finally 自然退出
    _is_running = False
    with _progress_lock:
        _screen_progress["phase"] = "done"
        _screen_progress["current"] = "已强制中止（后台请求可能仍在完成）"
        _screen_progress["pct"] = 0
    if obs:
        obs.error("web.api", "screen_stop",
                  "任务在 5s 内未响应停止信号，已强制重置 _is_running",
                  context={"hint": "工作线程可能卡在网络请求；后台会在请求结束后自然退出"})
    return jsonify({
        "code": 2,
        "msg": "⚠️ 任务 5 秒内未响应停止（多半卡在网络请求），已强制重置状态。后台线程会在请求结束后自然退出。",
        "stopped": False,
        "forced": True,
    })


@app.route("/api/data_status", methods=["GET"])
def api_data_status():
    """
    查询当前K线缓存状态。
    支持 market 参数（GET），按市场过滤股票总数，使分母与下载/筛选一致。
    优先读本地缓存文件（不调网络API），失败才降级到 get_stock_list()。
    """
    from ..data.fetcher import market_scanner
    from ..core.engine import ScreenEngine
    from pathlib import Path
    import json as _json

    market = request.args.get("market", "全部市场")
    logger.info(f"api_data_status: market=[{market}]")
    status = market_scanner.get_cache_status()
    # 用磁盘缓存数判断"数据是否就绪"（进程重启内存清空，但磁盘 CSV 仍在）
    cached = status.get("cached_count", 0)

    # 按市场过滤股票总数
    total_stocks = cached  # 兜底
    try:
        # 优先读本地缓存文件（不调网络，速度快）
        # 尝试两个可能的缓存文件路径
        base = Path(__file__).resolve().parent.parent
        cache_file = base / "data" / "cache" / "stocks.json"
        if not cache_file.exists():
            cache_file = base / "data" / "stocks.json"
        if cache_file.exists():
            with open(cache_file, "r", encoding="utf-8") as f:
                stocks = _json.load(f)
            raw_codes = [str(s.get("ts_code") or s.get("code") or "").strip()
                          for s in stocks if (s.get("ts_code") or s.get("code"))]
            if market != "全部市场":
                engine = ScreenEngine(market=market)
                filtered = engine._filter_by_market(raw_codes)
                total_stocks = len(filtered)
                logger.info(f"api_data_status: market=[{market}], 过滤后 {total_stocks} 只 (from cache file)")
            else:
                total_stocks = len(raw_codes)
        else:
            # 缓存文件不存在，降级到 get_stock_list（会调网络）
            from ..data.fetcher import get_stock_list
            all_stocks = get_stock_list()
            if market != "全部市场":
                engine = ScreenEngine(market=market)
                code_col = "代码" if "代码" in all_stocks.columns else "ts_code"
                raw_codes2 = all_stocks[code_col].astype(str).tolist()
                filtered = engine._filter_by_market(raw_codes2)
                total_stocks = len(filtered)
            else:
                total_stocks = len(all_stocks)
    except Exception as e:
        logger.error(f"api_data_status: 异常 {e}")
        total_stocks = cached  # 获取失败时以缓存数代替

    # 判断数据是否足够（覆盖全市场50%以上认为数据充足）
    data_ready = (total_stocks > 0 and cached >= total_stocks * 0.5) or cached >= 500
    if data_ready:
        hint = f"已缓存 {cached} 只 / {total_stocks} 只 ✅"
    elif cached > 0:
        hint = f"已缓存 {cached} 只 / {total_stocks} 只 ⚠️ 建议更新"
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
            "last_update": status.get("last_update"),
        }
    })


@app.route("/api/download", methods=["POST"])
def api_download():
    """
    单独下载/更新K线数据（不跑策略）。
    Body: { "force_refresh": false, "market": "主板" }
    前端轮询 /api/screen/progress 查看进度（phase=prefetch）。
    """
    body = request.get_json() or {}
    force = bool(body.get("force_refresh", False))
    market = body.get("market", "主板")  # ← 修复：获取 market 参数

    if _is_running:
        return jsonify({"code": 2, "msg": "当前有任务正在运行，请稍后再试"})

    def download_task():
        global _is_running
        try:
            # 超时检测：防止异常导致任务卡住
            if _is_running and (time.time() - _task_start_time) < _task_timeout:
                return
            _is_running = True
            _task_start_time = time.time()
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

            engine = get_engine(market)  # ← 修复：使用获取的 market 参数
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


# ── 通达信离线数据一键导入 ──────────────────────────────────────────
_tdx_import_progress: dict = {
    "phase": "idle",   # idle / download / extract / done / error / stopped
    "msg": "",
    "current": 0,
    "total": 0,
    "pct": 0,
    "result": None,
}
_tdx_import_lock = threading.Lock()
_tdx_import_running = False
_tdx_import_stop_event = threading.Event()


def _set_tdx_progress(phase: str, current: int, total: int, extra: dict) -> None:
    msg = (extra or {}).get("msg", "")
    if phase == "download":
        pct = int(current / total * 60) if total else 0
    elif phase == "extract":
        # 下载占 0-60%，解压占 60-99%
        pct = 60 + (int(current / total * 39) if total else 0)
    elif phase == "done":
        pct = 100
    elif phase in ("error", "stopped"):
        pct = 0
    else:
        pct = 0
    with _tdx_import_lock:
        _tdx_import_progress.update({
            "phase": phase,
            "msg": msg,
            "current": current,
            "total": total,
            "pct": min(pct, 100),
        })
        if "markets" in (extra or {}):
            _tdx_import_progress["markets"] = extra["markets"]


@app.route("/api/tdx/import", methods=["POST"])
def api_tdx_import():
    """
    一键下载并解压通达信沪深京日线数据包。
    下载源：https://data.tdx.com.cn/vipdoc/hsjday.zip
    目标目录：data/tdx_vipdoc/（覆盖式）
    前端轮询 /api/tdx/import/progress 查看进度。
    """
    global _tdx_import_running
    if _tdx_import_running:
        return jsonify({"code": 2, "msg": "已有通达信导入任务在执行中"})

    body = request.get_json(silent=True) or {}
    url = body.get("url") or None  # 允许前端覆盖
    keep_zip = bool(body.get("keep_zip", False))

    with _tdx_import_lock:
        _tdx_import_progress.update({
            "phase": "download",
            "msg": "准备开始下载...",
            "current": 0,
            "total": 0,
            "pct": 0,
            "result": None,
        })
        _tdx_import_progress.pop("markets", None)
    _tdx_import_stop_event.clear()

    def _run():
        global _tdx_import_running
        _tdx_import_running = True
        try:
            from ..data.import_tdx import import_tdx, TDX_HSJDAY_URL
            result = import_tdx(
                url=url or TDX_HSJDAY_URL,
                progress_cb=_set_tdx_progress,
                stop_event=_tdx_import_stop_event,
                keep_zip=keep_zip,
            )
            with _tdx_import_lock:
                _tdx_import_progress["result"] = result
                if result.get("status") == "ok":
                    _tdx_import_progress["phase"] = "done"
                    _tdx_import_progress["pct"] = 100
                    stats = result.get("stats", {})
                    _tdx_import_progress["msg"] = f"导入完成，共 {stats.get('total', 0)} 只股票"
                elif result.get("status") == "stopped":
                    _tdx_import_progress["phase"] = "stopped"
                    _tdx_import_progress["msg"] = "已取消"
                else:
                    _tdx_import_progress["phase"] = "error"
                    _tdx_import_progress["msg"] = result.get("error") or "导入失败"
        except Exception as e:
            logger.error(f"TDX 导入异常: {e}", exc_info=True)
            with _tdx_import_lock:
                _tdx_import_progress["phase"] = "error"
                _tdx_import_progress["msg"] = f"导入失败: {e}"
                _tdx_import_progress["result"] = {"status": "error", "error": str(e)}
        finally:
            _tdx_import_running = False
            _tdx_import_stop_event.clear()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return jsonify({"code": 0, "msg": "通达信离线数据导入已启动"})


@app.route("/api/tdx/import/progress", methods=["GET"])
def api_tdx_import_progress():
    with _tdx_import_lock:
        prog = dict(_tdx_import_progress)
    prog["running"] = _tdx_import_running
    return jsonify({"code": 0, "data": prog})


@app.route("/api/tdx/import/stop", methods=["POST"])
def api_tdx_import_stop():
    if not _tdx_import_running:
        return jsonify({"code": 1, "msg": "当前没有正在运行的导入任务"})
    _tdx_import_stop_event.set()
    return jsonify({"code": 0, "msg": "已发送停止信号"})


@app.route("/api/result", methods=["GET"])
def api_result():
    """获取最新筛选结果（运行中返回已完成的策略部分结果）"""
    if _is_running:
        with _partial_result_lock:
            partial = dict(_partial_results)
        with _current_strategies_lock:
            strategies_run = list(_current_strategies_run)
        data = {
            "strategies_run": strategies_run,
            "strategy_details": partial,
            "comprehensive_picks": [],
        }
        # 清理 NaN
        data = _clean_nan(data)
        return jsonify({"code": 2, "msg": "筛选中...", "data": data})
    if _last_result is None:
        return jsonify({"code": 1, "msg": "尚无筛选结果，请先调用 /api/screen", "data": None})
    # 清理 NaN
    result = _clean_nan(_last_result)
    return jsonify({"code": 0, "msg": "ok", "data": result})


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


@app.route("/api/diagnostics", methods=["GET"])
def api_diagnostics():
    """
    可观测性接口：返回最近的事件、错误统计、慢操作等。

    Query params:
        n        : 返回事件条数上限，默认 200
        level    : 过滤等级 debug/info/warn/error
        source   : 过滤源前缀，如 "data.fetch"
        summary  : 1 = 只返回摘要（不带事件列表）
    """
    try:
        from .observability import obs
        n = int(request.args.get("n", 200))
        level = request.args.get("level") or None
        source = request.args.get("source") or None
        only_summary = request.args.get("summary") in ("1", "true")
        data = {"summary": obs.summary()}
        if not only_summary:
            data["events"] = obs.recent(n=n, level=level, source_prefix=source)
        return jsonify({"code": 0, "data": data})
    except Exception as e:
        return jsonify({"code": 1, "msg": str(e)}), 500


@app.route("/api/diagnostics/clear", methods=["POST"])
def api_diagnostics_clear():
    """清空可观测性事件（不影响主流程）"""
    try:
        from .observability import obs
        obs.clear()
        return jsonify({"code": 0})
    except Exception as e:
        return jsonify({"code": 1, "msg": str(e)}), 500


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
            logger.debug("SSE generator ended (client disconnect or timeout)", exc_info=True)
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


# ══════════════════════════════════════════════════════════════════════
#  持仓 API
# ══════════════════════════════════════════════════════════════════════

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
                except Exception as e:
                    try:
                        from .observability import obs
                        obs.warn("web.portfolio", "fetch_price",
                                 f"获取实时价失败: {e}",
                                 context={"code": code, "action": "回退到 current_price"})
                    except Exception:
                        logger.debug("obs.warn fetch_price failed", exc_info=True)
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
    global _portfolio_signals_cache

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

        # ── 缓存检查 ──
        now = datetime.now()
        cache = _portfolio_signals_cache
        if cache["data"] and cache["timestamp"]:
            # 根据是否交易时间设置不同的TTL
            ttl = 5 if is_market_open() else 30
            age_minutes = (now - cache["timestamp"]).total_seconds() / 60
            if age_minutes < ttl:
                # 缓存有效，返回缓存数据
                logger.info(f"持仓信号命中缓存，{age_minutes:.1f}分钟前")
                result = cache["data"].copy()
                return jsonify({"code": 0, "data": result, "cached": True, "cache_age_minutes": round(age_minutes, 1)})

        results = []
        urgent_count = 0
        sell_count = 0
        reduce_count = 0

        # ── 并行获取持仓K线 ──
        def _fetch_kline(pos):
            code = pos["code"]
            # 并行获取实时价
            quote = get_stock_realtime(code)
            price = quote.get("最新价", 0)
            if price <= 0:
                price = pos.get("current_price", 0)
            if price <= 0:
                return None
            # 获取K线（MarketScanner会复用缓存）
            try:
                kl = market_scanner.get_history(code, days=60)
            except Exception as e:
                logger.warning(f"持仓详情获取K线失败 {code}: {e}（用空表兜底）")
                try:
                    obs.warn("web.portfolio", "fetch_history",
                             f"获取K线失败: {e}",
                             context={"code": code, "action": "用空 DataFrame 兜底"})
                except Exception:
                    logger.debug("obs.warn fetch_history failed", exc_info=True)
                kl = pd.DataFrame()
            return {
                "pos": pos,
                "price": price,
                "kl": kl,
            }

        with ThreadPoolExecutor(max_workers=min(10, len(pf.positions))) as pool:
            kline_results = list(pool.map(_fetch_kline, pf.positions))

        # 串行分析（逻辑轻量）
        for r in kline_results:
            if r is None:
                continue
            pos = r["pos"]
            price = r["price"]
            kl = r["kl"]

            sig = analyzer.analyze_position(
                code=pos["code"],
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

        # 更新缓存
        cache_data = {
            "signals": results,
            "urgent_count": urgent_count,
            "sell_count": sell_count,
            "reduce_count": reduce_count,
            "total_positions": len(pf.positions),
        }
        _portfolio_signals_cache["data"] = cache_data
        _portfolio_signals_cache["timestamp"] = datetime.now()

        return jsonify({"code": 0, "data": cache_data})
    except Exception as e:
        logger.error(f"卖出信号分析失败: {e}")
        return jsonify({"code": 1, "msg": str(e)})


@app.route("/api/portfolio/evaluate", methods=["POST"])
def api_portfolio_evaluate():
    """
    对持仓股票运行全部策略评分
    Body: { codes: ["000001.SZ", ...] }
    Returns: { code: 0, data: [ { ts_code, name, composite_score, strategies_hit:[...], ... } ] }
    """
    try:
        body = request.get_json() or {}
        codes = body.get("codes", [])
        if not codes:
            return jsonify({"code": 0, "data": []})

        engine = get_engine("主板")
        results = engine.evaluate_positions(codes)
        return jsonify({"code": 0, "data": results})
    except Exception as e:
        logger.error(f"持仓策略评估失败: {e}")
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
    # 启动自检：交易日历是否快用完。覆盖不足时先尝试 baostock 直连自动补全
    # （3.7.5 可用，不依赖 akshare），补全后仍不足才提醒手动更新节假日。
    try:
        from stock_screener.data.market_calendar import (
            check_calendar_coverage, refresh_calendar_from_baostock,
        )
        _cal_warn = check_calendar_coverage()
        if _cal_warn:
            logger.info(f"[交易日历] {_cal_warn} 尝试 baostock 自动补全…")
            if refresh_calendar_from_baostock():
                _cal_warn = check_calendar_coverage()  # 补全后复查
        if _cal_warn:
            logger.warning(f"[交易日历] {_cal_warn}")
            obs.warn("data.calendar", "coverage", _cal_warn,
                     context={"action": "更新 data/market_calendar.py 的 _STATIC_HOLIDAYS"})
    except Exception as e:
        logger.warning(f"交易日历自检失败: {e}")
    app.run(host=host, port=port, debug=debug, use_reloader=False)
