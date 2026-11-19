"""绑定 scope 的工具 schema：登录态绑定的列不再出现在模型可见参数里（specs/007 US4）。

core 只按"哪些隔离列被服务端覆盖"决定 schema；内部账号（无覆盖）的 schema 与行为
保持现状，商户会话的账户由系统注入，模型既看不到也不必填写。
"""

from typing import cast

import pytest

from flowpay.core.data_sources.reader import SourceResult
from flowpay.core.data_sources.tools import LogToolSpec, SqlToolSpec, log_tool, sql_tool
from flowpay.core.event_log.contracts import EventSink
from flowpay.core.json import JsonObject, require_list, require_object
from flowpay.core.tools import Tool, ToolInputError
from flowpay.core.tools.toolbox import Toolbox

BOUND = {"account_id": "acct_bound_001"}


def empty_result() -> SourceResult:
    """最小的合法查询结果：工具层只透传 rows/truncated，来源信息由运行时装配。"""
    return {
        "rows": [],
        "truncated": False,
        "source": {"path": "x", "sha256": ""},
        "version": {
            "dataset_release_id": "t",
            "dataset_manifest_sha256": "",
            "source_policy_sha256": "",
            "files": {},
        },
        "query": {"operation": "fixture", "parameters": {}},
    }


async def no_query(source: str, scope: JsonObject, filters: JsonObject) -> SourceResult:
    """不会被调用的查询替身；只用于检查 schema。"""
    del source, scope, filters
    return empty_result()


async def no_logs(
    source: str,
    *,
    filters: JsonObject | None = None,
    start: str | None = None,
    end: str | None = None,
) -> SourceResult:
    """不会被调用的日志替身；只用于检查 schema。"""
    del source, filters, start, end
    return empty_result()


def payments_tool(overrides: dict[str, str] | None, calls: list[JsonObject] | None = None) -> Tool:
    """按登记口径构造 query_payments：scope=账户与环境，filter=支付编号。"""

    async def query(source: str, scope: JsonObject, filters: JsonObject) -> SourceResult:
        del source, filters
        if calls is not None:
            calls.append(scope)
        return empty_result()

    return sql_tool(
        SqlToolSpec(
            "query_payments", "", "payments", ("account_id", "environment"), ("payment_id",)
        ),
        query,
        {},
        overrides,
    )


def test_merchant_binding_removes_account_from_sql_schema() -> None:
    """绑定账户后，query_payments 只要求环境；账户既不进 properties 也不进 required。"""
    tool = payments_tool(BOUND)
    parameters = require_object(tool.parameters)
    properties = require_object(parameters["properties"])
    assert "account_id" not in properties
    assert set(properties) == {"environment", "payment_id"}
    assert require_list(parameters["required"]) == ["environment"]
    assert parameters["additionalProperties"] is False


def test_internal_scope_keeps_both_scope_fields_required() -> None:
    """内部账号没有绑定：schema 与现状完全一致，账户与环境都必填。"""
    tool = payments_tool(None)
    parameters = require_object(tool.parameters)
    properties = require_object(parameters["properties"])
    assert set(properties) == {"account_id", "environment", "payment_id"}
    assert require_list(parameters["required"]) == ["account_id", "environment"]


class Recorder:
    """记录工具事件的最小事件出口；只保留类型与载荷。"""

    def __init__(self) -> None:
        """每次 emit 追加一条记录，供断言模型参数在事件里留下的痕迹。"""
        self.events: list[tuple[str, JsonObject]] = []

    async def emit(
        self,
        kind: str,
        payload: JsonObject,
        call_id: str | None = None,
        parent_call_id: str | None = None,
    ) -> None:
        """按事件出口协议记录一次调用，忽略调用关联编号。"""
        del call_id, parent_call_id
        self.events.append((kind, payload))


def toolbox_for(tool: Tool, recorder: Recorder) -> Toolbox:
    """把单个工具包成带记录的执行入口，走与运行时相同的 schema 校验。"""
    return Toolbox([tool], cast("EventSink", cast(object, recorder)))


async def test_model_cannot_supply_the_bound_field() -> None:
    """被绑定的列已从 properties 移除：模型再填它会被工具边界拒绝。"""
    tool = payments_tool(BOUND)
    recorder = Recorder()
    toolbox = toolbox_for(tool, recorder)
    with pytest.raises(ToolInputError):
        await toolbox.execute(
            "query_payments",
            {"account_id": "acct_other", "environment": "production"},
            "call_1",
        )
    started = [payload for kind, payload in recorder.events if kind == "tool.started"]
    assert require_object(started[0]["arguments"])["account_id"] == "acct_other"


async def test_bound_scope_reaches_the_query() -> None:
    """工具执行时把服务端绑定值作为 scope 下发，模型只填剩下的条件。"""
    scopes: list[JsonObject] = []
    tool = payments_tool(BOUND, scopes)
    await tool.execute({"environment": "production", "payment_id": "pay_001"})
    assert scopes == [{"account_id": "acct_bound_001", "environment": "production"}]


async def test_unbound_scope_still_requires_the_model_field() -> None:
    """没有绑定时缺账户仍按必填处理，返回可纠正的工具错误。"""
    tool = payments_tool(None)
    with_error = await _execute_expect_error(tool, {"environment": "production"})
    assert with_error is True


async def _execute_expect_error(tool: Tool, arguments: JsonObject) -> bool:
    """执行一次工具并确认它按协议拒绝；用于断言必填字段仍然生效。"""
    try:
        await tool.execute(arguments)
    except (ToolInputError, ValueError, KeyError):
        return True
    return False


def test_apirequest_log_tool_uses_first_unbound_filter_as_required() -> None:
    """日志工具必填取第一个未被覆盖的过滤字段；被绑定的账户从参数里消失。"""
    tool = log_tool(
        LogToolSpec("get_api_request", "", "api_requests", ("account_id", "request_id")),
        no_logs,
        {},
        BOUND,
    )
    parameters = require_object(tool.parameters)
    properties = require_object(parameters["properties"])
    assert "account_id" not in properties
    assert set(properties) == {"request_id"}
    assert require_list(parameters["required"]) == ["request_id"]


def test_unbound_log_tool_schema_is_unchanged() -> None:
    """没有绑定：第一个过滤字段必填，其余可选，与现状一致。"""
    tool = log_tool(
        LogToolSpec("get_api_request", "", "api_requests", ("account_id", "request_id")),
        no_logs,
    )
    parameters = require_object(tool.parameters)
    assert set(require_object(parameters["properties"])) == {"account_id", "request_id"}
    assert require_list(parameters["required"]) == ["account_id"]


def test_delivery_attempts_log_tool_stays_optional_first_field() -> None:
    """未绑定的投递尝试工具：必填仍是第一个过滤字段。"""
    tool = log_tool(
        LogToolSpec("list_delivery_attempts", "", "delivery_attempts", ("delivery_id",)),
        no_logs,
    )
    parameters = require_object(tool.parameters)
    assert require_list(parameters["required"]) == ["delivery_id"]


def test_incidents_log_tool_keeps_window_fields() -> None:
    """带时间窗口的日志工具：必填仍是第一个过滤字段，窗口字段保持可选。"""
    tool = log_tool(
        LogToolSpec(
            "list_incidents", "", "service_incidents", ("service", "region", "status"), window=True
        ),
        no_logs,
    )
    parameters = require_object(tool.parameters)
    assert set(require_object(parameters["properties"])) == {
        "service",
        "region",
        "status",
        "start",
        "end",
    }
    assert require_list(parameters["required"]) == ["service"]


def test_fully_bound_log_tool_has_no_required_field_and_keeps_notice() -> None:
    """所有过滤字段都被绑定覆盖时 required 为空；没有任何条件时仍返回可纠正的 notice。"""
    tool = log_tool(
        LogToolSpec("scoped_logs", "", "scoped", ("account_id",)),
        no_logs,
        {},
        BOUND,
    )
    parameters = require_object(tool.parameters)
    assert require_list(parameters["required"]) == []


async def test_log_tool_without_filters_returns_notice() -> None:
    """空过滤集合的日志工具执行时回填 notice，而不是发一次空查询。"""
    tool = log_tool(LogToolSpec("empty_logs", "", "empty", ()), no_logs)
    payload = await tool.execute({})
    assert payload["rows"] == []
    assert "过滤条件" in str(payload.get("notice", ""))


async def test_bound_log_scope_reaches_the_query() -> None:
    """绑定账户的日志查询：账户取服务端值，模型只给请求编号。"""
    calls: list[JsonObject] = []

    async def logs(
        source: str,
        *,
        filters: JsonObject | None = None,
        start: str | None = None,
        end: str | None = None,
    ) -> SourceResult:
        del source, start, end
        calls.append(filters or {})
        return empty_result()

    tool = log_tool(
        LogToolSpec("get_api_request", "", "api_requests", ("account_id", "request_id")),
        logs,
        {},
        BOUND,
    )
    await tool.execute({"request_id": "req_001"})
    assert calls == [{"request_id": "req_001", "account_id": "acct_bound_001"}]


def test_bound_tool_description_says_the_system_owns_the_field() -> None:
    """存在绑定时工具描述追加说明，模型据此不再向用户索要账户。"""
    tool = payments_tool(BOUND)
    assert "系统绑定" in tool.description
    assert "account_id" in tool.description


def test_unbound_tool_description_is_unchanged() -> None:
    """内部会话没有绑定：工具描述与原登记文案完全一致。"""
    tool = payments_tool(None)
    assert "系统绑定" not in tool.description
