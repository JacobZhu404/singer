"""baostock 登录熔断器状态转移测试。

baostock 登录失败是账号级/会话级的持久失败（尤其「黑名单用户」）：每只股票各重试
一次 login 会拖垮整轮下载。熔断器的契约：
  - 普通失败连续 N 次（_FAIL_THRESHOLD）才进入短冷却（_COOLDOWN_TRANSIENT）
  - 「黑名单」错误立刻进入长冷却（_COOLDOWN_BLACKLIST），本会话基本停用
  - 冷却期内 _ensure_login 直接返回 False（不再尝试 login）
用注入的单调时钟验证，避免依赖真实 time/网络。
"""

import pytest

from stock_screener.data import baostock_source as b


@pytest.fixture(autouse=True)
def _reset_breaker(monkeypatch):
    """每个用例前重置熔断器全局态，并冻结单调时钟在 1000.0。"""
    monkeypatch.setattr(b, "_login_failures", 0, raising=False)
    monkeypatch.setattr(b, "_login_cooldown_until", 0.0, raising=False)
    monkeypatch.setattr(b.time, "monotonic", lambda: 1000.0)
    yield


def test_transient_below_threshold_no_cooldown():
    """普通失败未达阈值：不进入冷却。"""
    b._trip_cooldown("服务器连接失败")
    b._trip_cooldown("服务器连接失败")  # 2 次 < 阈值 3
    assert b._login_failures == 2
    assert b._login_cooldown_until == 0.0


def test_transient_at_threshold_trips_short_cooldown():
    """普通失败达阈值：进入短冷却 _COOLDOWN_TRANSIENT。"""
    for _ in range(b._FAIL_THRESHOLD):
        b._trip_cooldown("服务器连接失败")
    assert b._login_failures == b._FAIL_THRESHOLD
    assert b._login_cooldown_until == pytest.approx(1000.0 + b._COOLDOWN_TRANSIENT)


def test_blacklist_trips_long_cooldown_immediately():
    """黑名单错误：首次即进入长冷却，无需累计。"""
    b._trip_cooldown("用户被列入黑名单")
    assert b._login_cooldown_until == pytest.approx(1000.0 + b._COOLDOWN_BLACKLIST)
    # 长冷却远大于短冷却
    assert b._COOLDOWN_BLACKLIST > b._COOLDOWN_TRANSIENT


def test_ensure_login_skips_during_cooldown():
    """冷却期内 _ensure_login 直接 False，不触碰网络。"""
    # 冷却终点在未来
    b._login_cooldown_until = 1000.0 + 100.0
    called = {"login": False}

    def _boom():
        called["login"] = True
        raise AssertionError("冷却期内不应调用 bs.login")

    # 即便 login 被打桩为爆炸，也不应被调用
    import baostock as bs
    bs_login = getattr(bs, "login", None)
    try:
        bs.login = _boom
        assert b._ensure_login() is False
        assert called["login"] is False
    finally:
        if bs_login is not None:
            bs.login = bs_login


def test_ensure_login_proceeds_after_cooldown_expires(monkeypatch):
    """冷却到期后 _ensure_login 不再被熔断拦截（进入真正登录路径）。"""
    # 冷却终点在过去（now=1000 > until=500）
    b._login_cooldown_until = 500.0
    b._bs_conn = None

    class _Conn:
        error_code = "0"

    monkeypatch.setattr(b.bs, "login", lambda: _Conn())
    assert b._ensure_login() is True
    # 成功登录后失败计数清零
    assert b._login_failures == 0
