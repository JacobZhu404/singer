# -*- coding: utf-8 -*-
"""
可观测性收集器：统一收集错误、跳过、重试、阶段耗时事件。

设计原则：
- 不打断主流程：所有 record_* 函数本身不抛异常
- 线程安全：内部锁保护
- 环形缓冲：默认保留最近 1000 条事件，避免内存膨胀
- 易于排查：记录足够上下文（错误信息、堆栈摘要、动作、耗时）

使用：
    from core.observability import obs

    obs.error("data.fetch", "fetch_one", f"failed: {e}",
              context={"code": "600519", "retry": 3, "action": "skip"})
    with obs.timer("stage", "prefetch_klines", context={"total": 317}):
        ... # 自动记录耗时
"""

import time
import logging
import threading
import traceback
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Deque, Dict, List, Optional, Any

logger = logging.getLogger(__name__)


# ── 事件级别 ───────────────────────────────────────────────────────────────

class Level:
    DEBUG = "debug"
    INFO = "info"
    WARN = "warn"
    ERROR = "error"


@dataclass
class Event:
    """单条可观测事件"""
    ts: float                                       # epoch 秒
    level: str
    source: str                                     # 模块/子系统，如 "data.fetch"
    op: str                                         # 操作名，如 "fetch_one"
    message: str                                    # 简要描述
    context: Dict[str, Any] = field(default_factory=dict)
    duration_ms: Optional[float] = None             # 耗时（毫秒），timer 自动填
    traceback: Optional[str] = None                 # 异常堆栈（最后 6 行）

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["ts_human"] = datetime.fromtimestamp(self.ts).strftime("%Y-%m-%d %H:%M:%S")
        return d


# ── 收集器主体 ──────────────────────────────────────────────────────────────

class Observability:
    """
    全局可观测性收集器。
    - 环形缓冲：默认保留 1000 条
    - 计数器：按 (level, source) 累计
    - 阶段耗时：保留最近 200 条带 duration 的事件，便于性能分析
    """

    def __init__(self, capacity: int = 1000):
        self._capacity = capacity
        self._events: Deque[Event] = deque(maxlen=capacity)
        self._counters: Dict[str, int] = {}
        self._lock = threading.Lock()

    # ── 记录接口 ──

    def _record(self, level: str, source: str, op: str, message: str,
                context: Optional[Dict[str, Any]] = None,
                duration_ms: Optional[float] = None,
                tb: Optional[str] = None) -> None:
        try:
            evt = Event(
                ts=time.time(),
                level=level,
                source=source,
                op=op,
                message=str(message)[:500],
                context=dict(context or {}),
                duration_ms=duration_ms,
                traceback=tb,
            )
            with self._lock:
                self._events.append(evt)
                key = f"{level}:{source}"
                self._counters[key] = self._counters.get(key, 0) + 1
        except Exception:
            # 严禁让观测层把主流程搞挂
            pass

    def debug(self, source: str, op: str, message: str, context: Optional[Dict[str, Any]] = None) -> None:
        self._record(Level.DEBUG, source, op, message, context)

    def info(self, source: str, op: str, message: str, context: Optional[Dict[str, Any]] = None) -> None:
        self._record(Level.INFO, source, op, message, context)

    def warn(self, source: str, op: str, message: str, context: Optional[Dict[str, Any]] = None) -> None:
        self._record(Level.WARN, source, op, message, context)

    def error(self, source: str, op: str, message: str,
              context: Optional[Dict[str, Any]] = None,
              exc: Optional[BaseException] = None) -> None:
        tb = None
        if exc is not None:
            try:
                tb_lines = traceback.format_exception(type(exc), exc, exc.__traceback__)
                # 只保留最后 6 行，避免日志爆炸
                tb = "".join(tb_lines[-6:])[:2000]
            except Exception:
                tb = None
        self._record(Level.ERROR, source, op, message, context, tb=tb)

    @contextmanager
    def timer(self, source: str, op: str, context: Optional[Dict[str, Any]] = None,
              level: str = Level.INFO):
        """上下文管理器：自动记录代码段耗时（毫秒）"""
        start = time.perf_counter()
        err: Optional[BaseException] = None
        try:
            yield
        except BaseException as e:
            err = e
            raise
        finally:
            dur_ms = (time.perf_counter() - start) * 1000.0
            if err is not None:
                self.error(source, op, f"{op} 异常: {err}",
                           context=context, exc=err)
                # 仍然把耗时记下来
                self._record(Level.ERROR, source, op, f"{op} 失败耗时", context, dur_ms)
            else:
                self._record(level, source, op, f"{op} 完成", context, dur_ms)

    # ── 查询接口 ──

    def recent(self, n: int = 200, level: Optional[str] = None,
               source_prefix: Optional[str] = None) -> List[Dict[str, Any]]:
        """返回最近 n 条事件（按时间倒序），可选按 level / source 前缀过滤"""
        with self._lock:
            evts = list(self._events)
        # 反向遍历
        out = []
        for e in reversed(evts):
            if level and e.level != level:
                continue
            if source_prefix and not e.source.startswith(source_prefix):
                continue
            out.append(e.to_dict())
            if len(out) >= n:
                break
        return out

    def counters(self) -> Dict[str, int]:
        with self._lock:
            return dict(self._counters)

    def summary(self) -> Dict[str, Any]:
        """系统当前可观测性摘要：按 level 计数 + 各 source 的错误率 + 慢操作 Top5"""
        with self._lock:
            evts = list(self._events)
            counters = dict(self._counters)

        # 按 level 汇总
        level_count: Dict[str, int] = {}
        # 按 source 错误数
        source_errors: Dict[str, int] = {}
        # 带耗时的操作
        slow_ops: List[Dict[str, Any]] = []
        for e in evts:
            level_count[e.level] = level_count.get(e.level, 0) + 1
            if e.level == Level.ERROR:
                source_errors[e.source] = source_errors.get(e.source, 0) + 1
            if e.duration_ms is not None:
                slow_ops.append({
                    "source": e.source, "op": e.op,
                    "duration_ms": round(e.duration_ms, 1),
                    "ts_human": datetime.fromtimestamp(e.ts).strftime("%H:%M:%S"),
                    "context": e.context,
                })
        slow_ops.sort(key=lambda x: x["duration_ms"], reverse=True)

        return {
            "total_events": len(evts),
            "capacity": self._capacity,
            "by_level": level_count,
            "errors_by_source": source_errors,
            "slow_ops_top5": slow_ops[:5],
            "counters": counters,
        }

    def clear(self) -> None:
        with self._lock:
            self._events.clear()
            self._counters.clear()


# 全局单例
obs = Observability(capacity=1000)
