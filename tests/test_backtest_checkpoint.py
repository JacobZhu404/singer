"""回测断点续跑：trade 序列化 / 签名 / checkpoint 落盘与加载。"""

import os
import json

from stock_screener.backtest.backtest_engine import (
    BacktestTrade,
    BacktestResult,
    _trade_to_dict,
    _trade_from_dict,
    _checkpoint_signature,
    _write_checkpoint,
    _load_checkpoint,
)


def _mk_trade(code="600000", strat="macd_bull"):
    return BacktestTrade(
        buy_date="20250625",
        code=code,
        name="测试股",
        strategy=strat,
        buy_price=10.5,
        score=88,
        signals=["金叉", "放量"],
        has_risk=False,
        returns={2: 1.23, 5: -0.4, 10: 3.1},
        exit_prices={2: 10.63, 5: 10.46, 10: 10.83},
        max_drawdowns={2: -0.5, 5: -1.2, 10: -0.8},
    )


def test_trade_dict_roundtrip_preserves_int_period_keys():
    t = _mk_trade()
    # 经 json 编解码（模拟落盘），int 键会变 str，反序列化须还原成 int
    d = json.loads(json.dumps(_trade_to_dict(t)))
    t2 = _trade_from_dict(d)
    assert t2.code == t.code
    assert t2.returns == {2: 1.23, 5: -0.4, 10: 3.1}
    assert all(isinstance(k, int) for k in t2.returns)
    assert t2.exit_prices == t.exit_prices
    assert t2.max_drawdowns == t.max_drawdowns
    assert t2.signals == ["金叉", "放量"]


def test_signature_stable_and_sensitive():
    dates = ["20250101", "20250108"]
    strats = ["a", "b"]
    codes = [("600000", "x"), ("000001", "y")]
    s1 = _checkpoint_signature(dates, strats, codes, top_n=10, filter_sell=True)
    # 顺序无关
    s2 = _checkpoint_signature(dates, ["b", "a"], list(reversed(codes)), top_n=10, filter_sell=True)
    assert s1 == s2
    # 参数变化 → 签名变化
    assert s1 != _checkpoint_signature(dates, strats, codes, top_n=20, filter_sell=True)
    assert s1 != _checkpoint_signature(dates + ["20250115"], strats, codes, top_n=10, filter_sell=True)


def test_checkpoint_write_load_roundtrip(tmp_path):
    path = str(tmp_path / "ckpt.json")
    sig = "abc123"
    results = {"macd_bull": BacktestResult(strategy="macd_bull")}
    results["macd_bull"].trades.append(_mk_trade())
    done = {"20250625"}
    _write_checkpoint(path, sig, done, results)
    assert os.path.exists(path)

    # 新一轮：空结果，加载 checkpoint 应回填 trades 并返回已完成日期
    fresh = {"macd_bull": BacktestResult(strategy="macd_bull")}
    loaded = _load_checkpoint(path, sig, fresh)
    assert loaded == {"20250625"}
    assert len(fresh["macd_bull"].trades) == 1
    assert fresh["macd_bull"].trades[0].returns == {2: 1.23, 5: -0.4, 10: 3.1}


def test_load_rejects_signature_mismatch(tmp_path):
    path = str(tmp_path / "ckpt.json")
    results = {"macd_bull": BacktestResult(strategy="macd_bull")}
    results["macd_bull"].trades.append(_mk_trade())
    _write_checkpoint(path, "sig_old", {"20250625"}, results)

    fresh = {"macd_bull": BacktestResult(strategy="macd_bull")}
    loaded = _load_checkpoint(path, "sig_new", fresh)
    assert loaded == set()
    assert len(fresh["macd_bull"].trades) == 0  # 不回填


def test_load_missing_file_returns_empty(tmp_path):
    fresh = {"x": BacktestResult(strategy="x")}
    assert _load_checkpoint(str(tmp_path / "nope.json"), "s", fresh) == set()
    assert len(fresh["x"].trades) == 0
