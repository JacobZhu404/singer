"""Web 路由烟测：用 Flask test_client 验证关键 GET 路由能加载、不抛异常。

不启动真实服务器；不依赖外部网络。仅探测 import 链 + 路由注册。
POST 路由不在本测试覆盖范围内（会触发后台筛选/下载，不适合烟测）。
"""

import pytest


@pytest.fixture(scope="module")
def client():
    from stock_screener.core.server import app
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_strategies_route(client):
    rv = client.get("/api/strategies")
    assert rv.status_code == 200
    body = rv.get_json()
    assert body and body.get("code") == 0
    assert isinstance(body.get("data"), list)
    assert len(body["data"]) > 0


def test_screen_progress_route(client):
    rv = client.get("/api/screen/progress")
    assert rv.status_code == 200
    body = rv.get_json()
    assert body.get("code") == 0
    assert "data" in body


def test_data_status_route(client):
    rv = client.get("/api/data_status")
    assert rv.status_code == 200
    body = rv.get_json()
    assert body.get("code") == 0


def test_status_route(client):
    rv = client.get("/api/status")
    assert rv.status_code == 200


def test_diagnostics_route_with_events(client):
    rv = client.get("/api/diagnostics?n=10")
    assert rv.status_code == 200
    body = rv.get_json()
    assert body.get("code") == 0
    data = body.get("data", {})
    assert "events" in data
    assert "summary" in data
    assert isinstance(data["events"], list)


def test_diagnostics_route_summary_only(client):
    rv = client.get("/api/diagnostics?summary=1")
    assert rv.status_code == 200
    body = rv.get_json()
    assert body.get("code") == 0
    data = body.get("data", {})
    assert "summary" in data
    # summary 模式下不返回 events
    assert "events" not in data


def test_diagnostics_filter_by_level(client):
    rv = client.get("/api/diagnostics?level=ERROR&n=5")
    assert rv.status_code == 200


def test_result_route(client):
    rv = client.get("/api/result")
    # 可能没有上次结果，但路由本身应该响应
    assert rv.status_code == 200


def test_portfolio_route(client):
    rv = client.get("/api/portfolio")
    assert rv.status_code == 200
    body = rv.get_json()
    assert body.get("code") == 0


def test_screen_stop_when_idle(client):
    """无任务运行时 stop 应返回 code=1 + 明确文案，不阻塞 5 秒。"""
    import time
    t0 = time.time()
    rv = client.post("/api/screen/stop")
    elapsed = time.time() - t0
    assert rv.status_code == 200
    body = rv.get_json()
    assert body.get("code") == 1
    assert "没有正在运行" in body.get("msg", "")
    # 空闲态必须立即返回，绝不能进入 5s 等待
    assert elapsed < 0.5, f"空闲态 stop 不该等待，实际 {elapsed:.2f}s"
