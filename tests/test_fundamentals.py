"""基本面（PE/PB）模块测试：风险标签 + 分数调整 + 磁盘缓存"""

import json
import os
from unittest.mock import patch

import pytest

from stock_screener.utils.fundamental_flags import (
    compute_fundamental_flags,
    fundamental_score_adjustment,
)
from stock_screener.data import fundamentals as fmod


# ── 风险标签 ────────────────────────────────────────────────

def test_pe_negative_emits_danger():
    flags = compute_fundamental_flags(pe=-5.0, pb=1.0)
    types = {f["type"] for f in flags}
    assert "pe_negative" in types
    assert next(f for f in flags if f["type"] == "pe_negative")["level"] == "danger"


def test_pe_extreme_emits_danger():
    flags = compute_fundamental_flags(pe=150.0, pb=2.0)
    types = {f["type"] for f in flags}
    assert "pe_extreme" in types


def test_pe_high_emits_warn():
    flags = compute_fundamental_flags(pe=60.0, pb=2.0)
    assert any(f["type"] == "pe_high" and f["level"] == "warn" for f in flags)


def test_normal_pe_pb_emits_nothing():
    assert compute_fundamental_flags(pe=15.0, pb=1.5) == []


def test_pb_high_emits_warn():
    flags = compute_fundamental_flags(pe=20.0, pb=15.0)
    assert any(f["type"] == "pb_high" for f in flags)


def test_none_inputs_safe():
    assert compute_fundamental_flags(pe=None, pb=None) == []


# ── 分数调整 ────────────────────────────────────────────────

def test_negative_pe_penalized():
    adj, reasons = fundamental_score_adjustment(pe=-3.0, pb=1.0)
    assert adj < 0
    assert any("亏损" in r for r in reasons)


def test_extreme_pe_penalized_less_than_loss():
    """约定：亏损公司罚得最重，其次是极高 PE"""
    loss_adj, _ = fundamental_score_adjustment(pe=-3.0, pb=1.0)
    high_adj, _ = fundamental_score_adjustment(pe=150.0, pb=1.0)
    assert loss_adj < high_adj < 0


def test_normal_pe_pb_no_adjustment():
    adj, reasons = fundamental_score_adjustment(pe=15.0, pb=1.5)
    assert adj == 0.0
    assert reasons == []


def test_only_penalty_never_bonus():
    """设计意图：估值维度只罚不奖，避免成为价值因子"""
    for pe in [5, 10, 15, 20, 30]:
        for pb in [0.5, 1.0, 2.0, 3.0]:
            adj, _ = fundamental_score_adjustment(pe=pe, pb=pb)
            assert adj <= 0, f"PE={pe} PB={pb} 给出了正向加成 {adj}"


# ── 磁盘缓存 ────────────────────────────────────────────────

def test_disk_cache_roundtrip(tmp_path, monkeypatch):
    cache_file = tmp_path / "fundamentals.json"
    monkeypatch.setattr(fmod, "_CACHE_PATH", str(cache_file))

    sample = {"600000": {"pe": 6.1, "pb": 0.41, "mktcap_wan": 1e7, "nmc_wan": 1e7, "turnover": 0.25}}
    fmod._save_disk_cache(sample)

    loaded = fmod._load_disk_cache()
    assert loaded["data"] == sample
    assert "_fetched_date" in loaded


def test_load_or_fetch_uses_today_cache(tmp_path, monkeypatch):
    """当日缓存命中时不再调网络"""
    cache_file = tmp_path / "fundamentals.json"
    monkeypatch.setattr(fmod, "_CACHE_PATH", str(cache_file))

    sample = {"600000": {"pe": 6.1, "pb": 0.41, "mktcap_wan": 1e7, "nmc_wan": 1e7, "turnover": 0.0}}
    fmod._save_disk_cache(sample)

    with patch.object(fmod, "fetch_market_fundamentals") as mock_fetch:
        result = fmod.load_or_fetch_fundamentals()
        mock_fetch.assert_not_called()
    assert result == sample


def test_load_or_fetch_falls_back_to_stale_on_network_fail(tmp_path, monkeypatch):
    """实时抓取失败 → 回退到最近一份磁盘缓存（哪怕日期已过）"""
    cache_file = tmp_path / "fundamentals.json"
    monkeypatch.setattr(fmod, "_CACHE_PATH", str(cache_file))

    # 写一份「过期」的缓存（日期改成昨天）
    sample = {"600000": {"pe": 6.1, "pb": 0.41, "mktcap_wan": 1e7, "nmc_wan": 1e7, "turnover": 0.0}}
    fmod._save_disk_cache(sample)
    cached = json.loads(cache_file.read_text(encoding="utf-8"))
    cached["_fetched_date"] = "2020-01-01"
    cache_file.write_text(json.dumps(cached), encoding="utf-8")

    with patch.object(fmod, "fetch_market_fundamentals", return_value={}):
        result = fmod.load_or_fetch_fundamentals()
    assert result == sample  # 回退到了过期缓存


def test_load_or_fetch_returns_empty_when_no_cache_and_network_fails(tmp_path, monkeypatch):
    cache_file = tmp_path / "fundamentals.json"
    monkeypatch.setattr(fmod, "_CACHE_PATH", str(cache_file))
    with patch.object(fmod, "fetch_market_fundamentals", return_value={}):
        result = fmod.load_or_fetch_fundamentals()
    assert result == {}
