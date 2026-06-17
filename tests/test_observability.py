"""可观测性收集器测试"""
from stock_screener.core.observability import Observability, Level


def test_record_and_recent():
    obs = Observability(capacity=10)
    obs.error("data.fetch", "fetch_one", "boom", context={"code": "600519"})
    obs.warn("data.fetch", "downgrade", "slow", context={"to": 5})
    obs.info("engine", "start", "go")

    events = obs.recent(n=10)
    assert len(events) == 3
    # 倒序
    assert events[0]["op"] == "start"
    assert events[1]["op"] == "downgrade"
    assert events[2]["op"] == "fetch_one"


def test_filter_by_level():
    obs = Observability()
    obs.error("a", "x", "e1")
    obs.warn("a", "x", "w1")
    errs = obs.recent(level=Level.ERROR)
    assert len(errs) == 1
    assert errs[0]["level"] == "error"


def test_filter_by_source_prefix():
    obs = Observability()
    obs.info("data.fetch", "a", "1")
    obs.info("data.cache", "b", "2")
    obs.info("engine.run", "c", "3")
    out = obs.recent(source_prefix="data.")
    assert len(out) == 2
    assert all(e["source"].startswith("data.") for e in out)


def test_capacity_evicts_oldest():
    obs = Observability(capacity=3)
    for i in range(5):
        obs.info("s", f"op{i}", "msg")
    events = obs.recent(n=10)
    assert len(events) == 3
    # 最新 3 条：op4, op3, op2（倒序）
    ops = [e["op"] for e in events]
    assert ops == ["op4", "op3", "op2"]


def test_timer_records_duration():
    import time
    obs = Observability()
    with obs.timer("s", "fast"):
        time.sleep(0.01)
    events = obs.recent()
    assert len(events) == 1
    assert events[0]["duration_ms"] is not None
    assert events[0]["duration_ms"] >= 10  # at least 10ms


def test_timer_records_exception():
    obs = Observability()
    try:
        with obs.timer("s", "boom"):
            raise ValueError("nope")
    except ValueError:
        pass
    # 至少有一条 error 事件
    errs = obs.recent(level=Level.ERROR)
    assert len(errs) >= 1
    assert any("nope" in e["message"] for e in errs)


def test_summary_counts_levels():
    obs = Observability()
    obs.error("s", "a", "1")
    obs.error("s", "b", "2")
    obs.warn("s", "c", "3")
    summary = obs.summary()
    assert summary["by_level"]["error"] == 2
    assert summary["by_level"]["warn"] == 1
    assert summary["errors_by_source"]["s"] == 2


def test_record_silent_on_bad_input():
    """obs._record 不能因为 context 序列化问题等抛出"""
    obs = Observability()
    # context 包含不能 deepcopy 的对象不应抛出
    obs.error("s", "op", "msg", context={"weird": object()})
    events = obs.recent()
    assert len(events) == 1
