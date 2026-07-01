"""
「死票卫生」测试：验证 133-死票事故的三处结构性修复。

回归目标：
  - clean_nontrading_bars.py 遇到全被判非交易日的文件时删 CSV+pop meta（不留 header-only 空壳）
  - MarketScanner.prefetch_batch 结束时对「无历史+全源失败」的代码自动 flag 进 blocklist
  - local_cache.get_cached_codes 过滤 records<=0 与 header-only 文件
"""

import json
import os
import subprocess
import sys
import tempfile
from unittest.mock import patch

import pytest


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture
def isolated_cache(monkeypatch, tmp_path):
    """把 local_cache 的目录常量指到临时目录，避免污染真实缓存。"""
    from stock_screener.data import local_cache

    kline_dir = tmp_path / "klines"
    kline_dir.mkdir()
    meta_file = tmp_path / "meta.json"

    monkeypatch.setattr(local_cache, "_KLINE_DIR", str(kline_dir))
    monkeypatch.setattr(local_cache, "_META_FILE", str(meta_file))
    # 内存缓存清空避免污染
    monkeypatch.setattr(local_cache, "_kline_memory_cache", {})
    return {"kline_dir": kline_dir, "meta_file": meta_file}


# ─── B1: clean_nontrading_bars 空壳清理 ─────────────────────────────────────

def test_clean_nontrading_bars_deletes_shell_when_all_dropped(tmp_path):
    """如果一个 CSV 里所有行都被判非交易日，脚本应删 CSV 而非留下 header-only 空壳。"""
    kline_dir = tmp_path / "klines"
    kline_dir.mkdir()
    meta_file = tmp_path / "meta.json"

    # 造一个「所有行都在周末」的 CSV
    csv_path = kline_dir / "999999.csv"
    csv_path.write_text(
        "date,open,high,low,close,vol\n"
        "2026-06-27,10,10,10,10,100\n"   # 周六
        "2026-06-28,10,10,10,10,100\n",  # 周日
        encoding="utf-8",
    )
    meta_file.write_text(json.dumps({"999999": {"records": 2, "end_date": "20260628"}}),
                         encoding="utf-8")

    # 调用 scan_and_clean（打补丁把它指到临时目录）
    from stock_screener.scripts import clean_nontrading_bars as script

    with patch.object(script, "KLINES_DIR", str(kline_dir)), \
         patch.object(script, "META_PATH", str(meta_file)):
        stats = script.scan_and_clean(apply_changes=True)

    assert not csv_path.exists(), "全非交易日的 CSV 应被删除，而不是留空壳"
    meta_after = json.loads(meta_file.read_text(encoding="utf-8"))
    assert "999999" not in meta_after, "meta 也应 pop 掉"
    assert stats.get("deleted_files", 0) == 1


def test_clean_nontrading_bars_keeps_partial_valid(tmp_path):
    """混合行的 CSV 只丢非交易日行，正常行保留。"""
    kline_dir = tmp_path / "klines"
    kline_dir.mkdir()
    meta_file = tmp_path / "meta.json"

    csv_path = kline_dir / "999998.csv"
    csv_path.write_text(
        "date,open,high,low,close,vol\n"
        "2026-06-27,10,10,10,10,100\n"   # 周六 → 丢
        "2026-06-30,20,20,20,20,200\n",  # 周二 → 留
        encoding="utf-8",
    )
    meta_file.write_text(json.dumps({"999998": {"records": 2, "end_date": "20260630"}}),
                         encoding="utf-8")

    from stock_screener.scripts import clean_nontrading_bars as script

    with patch.object(script, "KLINES_DIR", str(kline_dir)), \
         patch.object(script, "META_PATH", str(meta_file)):
        script.scan_and_clean(apply_changes=True)

    assert csv_path.exists(), "有有效行的 CSV 必须保留"
    remaining = csv_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(remaining) == 2  # header + 1 valid row
    meta_after = json.loads(meta_file.read_text(encoding="utf-8"))
    assert meta_after["999998"]["records"] == 1


# ─── B3: get_cached_codes 过滤僵尸条目 ──────────────────────────────────────

def test_get_cached_codes_skips_zero_records(isolated_cache):
    """meta 里 records<=0 的代码不应出现在结果中。"""
    from stock_screener.data import local_cache

    # 建两个 CSV：一个有内容，一个 records=0
    (isolated_cache["kline_dir"] / "000001.csv").write_text(
        "date,open,high,low,close,vol\n2026-06-30,10,10,10,10,100\n"
    )
    (isolated_cache["kline_dir"] / "000002.csv").write_text(
        "date,open,high,low,close,vol\n2026-06-30,10,10,10,10,100\n"
    )
    isolated_cache["meta_file"].write_text(json.dumps({
        "000001": {"records": 1, "end_date": "20260630"},
        "000002": {"records": 0, "end_date": ""},
    }))

    codes = local_cache.get_cached_codes()
    assert "000001" in codes
    assert "000002" not in codes


def test_get_cached_codes_skips_header_only_shell(isolated_cache):
    """meta 缺失时，CSV 只有 header 也要被跳过（size 兜底）。"""
    from stock_screener.data import local_cache

    (isolated_cache["kline_dir"] / "000003.csv").write_text(
        "date,open,high,low,close,vol\n"  # 29 字节 header-only
    )
    (isolated_cache["kline_dir"] / "000004.csv").write_text(
        "date,open,high,low,close,vol\n2026-06-30,10,10,10,10,100\n"
    )
    isolated_cache["meta_file"].write_text("{}")

    codes = local_cache.get_cached_codes()
    assert "000003" not in codes, "header-only 空壳必须过滤"
    assert "000004" in codes


# ─── B2: prefetch_batch 结束自动 flag 死码 ─────────────────────────────────

def test_prefetch_batch_auto_flags_dead_codes(monkeypatch, tmp_path):
    """
    模拟一个「无历史 + 所有源返回 None」的代码；prefetch_batch 结束应把它自动登记进 blocklist。
    """
    from stock_screener.data import fetcher, local_cache

    # 隔离 blocklist 到临时文件
    reg_path = str(tmp_path / "delisted.json")
    reg = fetcher._DelistedRegistry(reg_path)
    monkeypatch.setattr(fetcher, "delisted_registry", reg)

    # 隔离 kline dir + meta（要让 _load_meta 返回 records=0）
    kline_dir = tmp_path / "klines"
    kline_dir.mkdir()
    meta_file = tmp_path / "meta.json"
    meta_file.write_text(json.dumps({"999977": {"records": 0, "end_date": ""}}))
    monkeypatch.setattr(local_cache, "_KLINE_DIR", str(kline_dir))
    monkeypatch.setattr(local_cache, "_META_FILE", str(meta_file))
    monkeypatch.setattr(local_cache, "_kline_memory_cache", {})

    # 拦截 data_layer.data_fetcher.get_kline 让所有源都返回 None
    from stock_screener.data import data_layer

    class _NullFetcher:
        def get_kline(self, code, days, meta=None):
            return None

    monkeypatch.setattr(data_layer, "data_fetcher", _NullFetcher())

    # 拦截 tencent 批量报价（快速路径）返回空，让所有代码退回 _fetch_round
    from stock_screener.data import tencent_batch
    monkeypatch.setattr(tencent_batch, "get_realtime_fast", lambda codes, max_workers=5: {})

    scanner = fetcher.MarketScanner()
    scanner._loaded = True

    # 触发 prefetch_batch，参数刻意小，缩短测试时间
    result = scanner.prefetch_batch(["999977"], days=30, max_workers=2)

    assert "999977" in result["failed"], "无源代码应在 failed 中"
    assert "999977" in reg.active_blocked(), "无历史+全源失败的代码应自动进 blocklist"
