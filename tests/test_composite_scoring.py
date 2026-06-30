"""组合打分核心数学测试。

`tools/composite_backtest._compose_one_date` 是 `engine.merge_results` 确定性核心的
纯函数复刻（无 I/O），因此用它锁定打分数学的回归：
  - 组内去重「头部全额 + 其余 ×INTRA_GROUP_DECAY」
  - 实测胜率因子 win_factor 线性加权
  - 跨组共识 n_groups（组内重复不额外加分）
  - 质量门槛 min_single / min_weighted
  - 排序键 (n_groups, composite) 降序
这些是 engine 与回测共用 constants 的单一事实来源，任何漂移都会在此处暴露。
"""

from stock_screener.tools.composite_backtest import (
    _rescore_strategy,
    _compose_one_date,
    W_WEIGHTED,
    W_RANK,
    W_CONSENSUS,
)
from stock_screener.core.constants import (
    INTRA_GROUP_DECAY,
    RANK_BONUS_MAX,
)


def test_rescore_single_hit_full_bonus():
    """单只命中：rank_pct=0，重打分 = raw*0.6 + 40。"""
    out = _rescore_strategy([("000001", 100.0)])
    assert out == [("000001", round(100 * 0.6 + 40, 1))]


def test_rescore_orders_and_decays_bonus():
    """多只命中：头部满额 rank 加成，尾部递减。"""
    out = _rescore_strategy([("a", 50.0), ("b", 90.0), ("c", 70.0)])
    # 应按重打分降序；b raw 最高且 rank1 → 最高分
    codes = [c for c, _ in out]
    assert codes[0] == "b"
    scores = dict(out)
    assert scores["b"] > scores["c"] > scores["a"]


def _wf(x):
    return {"_only": x}


def test_intra_group_decay_applied_within_group():
    """同组两策略命中同一只：组内头部全额，第二个 ×DECAY。"""
    hits = {
        "s1": [("X", 100.0)],
        "s2": [("X", 100.0)],
    }
    weights = {"s1": 1.0, "s2": 1.0}
    win_factors = {"s1": 1.0, "s2": 1.0}
    groups = {"s1": "G", "s2": "G"}  # 同组
    picks = _compose_one_date(hits, weights, win_factors, groups,
                              min_single=0, min_weighted=0, top_n=10)
    assert len(picks) == 1
    code, n_groups, composite = picks[0]
    assert code == "X"
    assert n_groups == 1  # 同组只算一个跨组命中

    # 单策略重打分：raw=100 单只 → 100*0.6+40 = 100
    rescored = 100.0
    # 单策略 rank_pct = 1/1 = 1 → rank_bonus = 0
    contrib = rescored * 1.0 * 1.0 + 0.0
    # 组内：头部全额 + 第二个 ×DECAY
    weighted = contrib + INTRA_GROUP_DECAY * contrib
    expected = weighted * W_WEIGHTED + (1 - 1.0) * W_RANK + 1 * W_CONSENSUS
    assert abs(composite - expected) < 1e-6


def test_cross_group_consensus_counts_groups_not_hits():
    """两策略命中同一只但属不同组：n_groups=2，组内无衰减。"""
    hits = {
        "s1": [("X", 100.0)],
        "s2": [("X", 100.0)],
    }
    weights = {"s1": 1.0, "s2": 1.0}
    win_factors = {"s1": 1.0, "s2": 1.0}
    groups = {"s1": "G1", "s2": "G2"}  # 不同组
    picks = _compose_one_date(hits, weights, win_factors, groups,
                              min_single=0, min_weighted=0, top_n=10)
    code, n_groups, composite = picks[0]
    assert n_groups == 2

    contrib = 100.0  # 同上单只重打分、rank_bonus=0
    weighted = contrib + contrib  # 两组各自头部全额，无衰减
    expected = weighted * W_WEIGHTED + (1 - 1.0) * W_RANK + 2 * W_CONSENSUS
    assert abs(composite - expected) < 1e-6


def test_win_factor_scales_contribution():
    """win_factor 直接线性缩放贡献。"""
    hits = {"s1": [("X", 100.0)]}
    weights = {"s1": 1.0}
    groups = {"s1": "G"}
    base = _compose_one_date(hits, weights, {"s1": 1.0}, groups, 0, 0, 10)[0][2]
    hi = _compose_one_date(hits, weights, {"s1": 1.5}, groups, 0, 0, 10)[0][2]
    # contrib 1.5x → weighted 1.5x → composite 的 weighted 部分按比例放大
    # composite = weighted*0.5 + 0 + 1*5; weighted_base=100 → base=55, hi=100*1.5*0.5+5=80
    assert abs(base - (100 * 0.5 + 5)) < 1e-6
    assert abs(hi - (150 * 0.5 + 5)) < 1e-6


def test_min_single_filters_low_score_hits():
    """单策略门槛过滤：raw 太低重打分后仍 < min_single 则剔除。"""
    hits = {"s1": [("LOW", 1.0)]}  # 1*0.6+40=40.6 重打分
    weights = {"s1": 1.0}
    win_factors = {"s1": 1.0}
    groups = {"s1": "G"}
    # min_single=50 > 40.6 → 全部过滤 → 无候选
    picks = _compose_one_date(hits, weights, win_factors, groups, 50, 0, 10)
    assert picks == []
    # min_single=20 < 40.6 → 保留
    picks2 = _compose_one_date(hits, weights, win_factors, groups, 20, 0, 10)
    assert len(picks2) == 1


def test_min_weighted_filters_aggregate():
    """加权总分门槛：weighted < min_weighted 剔除。"""
    hits = {"s1": [("X", 100.0)]}  # weighted = 100
    weights = {"s1": 1.0}
    win_factors = {"s1": 1.0}
    groups = {"s1": "G"}
    assert _compose_one_date(hits, weights, win_factors, groups, 0, 200, 10) == []
    assert len(_compose_one_date(hits, weights, win_factors, groups, 0, 50, 10)) == 1


def test_sort_key_prioritizes_n_groups_then_composite():
    """排序键：先按 n_groups，再按 composite，降序。"""
    hits = {
        "s1": [("MULTI", 60.0), ("SOLO", 100.0)],
        "s2": [("MULTI", 60.0)],
    }
    weights = {"s1": 1.0, "s2": 1.0}
    win_factors = {"s1": 1.0, "s2": 1.0}
    groups = {"s1": "G1", "s2": "G2"}  # MULTI 跨两组
    picks = _compose_one_date(hits, weights, win_factors, groups, 0, 0, 10)
    # MULTI n_groups=2 应排在 SOLO n_groups=1 之前，即便 SOLO raw 更高
    assert picks[0][0] == "MULTI"
    assert picks[0][1] == 2
    assert picks[1][0] == "SOLO"
    assert picks[1][1] == 1


def test_top_n_truncation():
    hits = {"s1": [(f"c{i}", 100.0 - i) for i in range(20)]}
    weights = {"s1": 1.0}
    win_factors = {"s1": 1.0}
    groups = {"s1": "G"}
    picks = _compose_one_date(hits, weights, win_factors, groups, 0, 0, 5)
    assert len(picks) == 5


def test_rank_bonus_uses_constant():
    """rank_bonus 用 RANK_BONUS_MAX，头部(rank_pct→0)接近满额。"""
    # 两只命中：rank1 rank_pct=1/2，rank2 rank_pct=2/2
    hits = {"s1": [("hi", 100.0), ("lo", 90.0)]}
    weights = {"s1": 1.0}
    win_factors = {"s1": 1.0}
    groups = {"s1": "G"}
    picks = _compose_one_date(hits, weights, win_factors, groups, 0, 0, 10)
    d = {c: comp for c, _, comp in picks}
    # hi 的 rank_bonus = (1-0.5)*RANK_BONUS_MAX > lo 的 (1-1.0)*RANK_BONUS_MAX = 0
    assert RANK_BONUS_MAX > 0
    assert d["hi"] > d["lo"]
