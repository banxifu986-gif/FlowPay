"""cap_query_keys：查询键封顶的保留策略与集合语义（specs/004 US7）。"""

from flowpay.workflows.manual_loop import QUERY_KEYS_CAP, cap_query_keys


def keys(prefix: str, count: int) -> set[str]:
    """构造 count 个可排序的查询键。"""
    return {f"{prefix}_{index:03d}" for index in range(count)}


def test_under_cap_returns_same_set() -> None:
    """未超限：原集合原样返回（原地语义，无淘汰）。"""
    done = keys("query_payments", 10)
    assert cap_query_keys(done, set(), QUERY_KEYS_CAP) is done
    assert len(done) == 10


def test_referenced_keys_always_survive() -> None:
    """当前 findings/actions 引用的键 100% 保留，即使它们最旧。"""
    done = keys("query_payments", 10)
    referenced = {"query_payments_000", "query_payments_001"}
    cap_query_keys(done, referenced, 5)
    assert referenced <= done
    assert len(done) == 5


def test_non_referenced_evicted_from_oldest() -> None:
    """其余键按 sorted 序保尾部：字典序最小的先淘汰。"""
    done = keys("query_payments", 10)
    cap_query_keys(done, set(), 5)
    assert done == {f"query_payments_{index:03d}" for index in range(5, 10)}


def test_referenced_over_cap_keeps_referenced_tail() -> None:
    """referenced 自身超 cap（现实不可达，findings 已封顶）：保 referenced 尾部。"""
    done = keys("query_payments", 30)
    referenced = keys("query_payments", 10)
    cap_query_keys(done, referenced, 5)
    assert done == {f"query_payments_{index:03d}" for index in range(5, 10)}


def test_zero_cap_is_noop() -> None:
    """cap<=0：不封顶（守卫分支，现行为语义）。"""
    done = keys("query_payments", 10)
    cap_query_keys(done, set(), 0)
    assert len(done) == 10
