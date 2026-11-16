"""元数据过滤渲染：缺省值兜底只作用于登记键（specs/005 US3）。"""

from psycopg.types.json import Jsonb

from flowpay.core.knowledge.store import metadata_condition


def test_no_filters_produce_no_condition() -> None:
    """没有过滤条件时不拼 SQL，也不带绑定参数（内部账号的全量检索）。"""
    assert metadata_condition(None) == ("", [])
    assert metadata_condition({}) == ("", [])


def test_registered_key_falls_back_to_default() -> None:
    """audience 缺键按 external 处理：旧集合与新集合都走同一条 SQL。"""
    sql, values = metadata_condition({"audience": "external"})
    assert "COALESCE(d.metadata->>'audience', %s) = %s" in sql
    assert values == ["external", "external"]
    sql, values = metadata_condition({"audience": "internal"})
    assert values == ["external", "internal"]


def test_unregistered_keys_stay_strict() -> None:
    """其他元数据字段仍是严格包含匹配，不因文档少写字段而放宽。"""
    sql, values = metadata_condition({"doc_type": "api_doc"})
    assert "d.metadata @> %s::jsonb" in sql
    assert len(values) == 1 and isinstance(values[0], Jsonb)
    sql, values = metadata_condition({"audience": "external", "doc_type": "api_doc"})
    assert "COALESCE" in sql and "@>" in sql
    assert len(values) == 3


def test_unregistered_keys_are_never_interpolated() -> None:
    """只有白名单键会拼进 SQL 文本；其余键（含引号）只作为 jsonb 绑定值（F6）。"""
    sql, values = metadata_condition({"a'b": "x"})
    assert "a'b" not in sql
    assert len(values) == 1
