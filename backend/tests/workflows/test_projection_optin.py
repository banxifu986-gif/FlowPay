"""两阶段 opt-in 的投影行为（T020-T022）：worker 请求含占位、Send 隔离、轮间用量独立。

全部走 multi-agent 的文档路径（无业务数据源依赖）；Send 以 max_concurrency=1
串行化来固定受控模型的 FIFO 响应顺序，隔离断言关注请求内容而非时序。
"""

import json
from pathlib import Path

from openai.types.chat import ChatCompletion

from flowpay.core.chat.projection import ContextPolicy
from flowpay.core.chat.types import SessionView
from flowpay.core.config import Settings
from flowpay.core.event_log import EventStore
from flowpay.core.execution.manager import SessionManager
from flowpay.core.execution.resources import RuntimeResources
from flowpay.core.json import JsonObject
from flowpay.core.llm.embedding.ollama import retrieval_input
from flowpay.workflows.multi_agent import MultiAgentWorkflow

from .fixtures import Model, Search, completion, intent_call, read_call, search_call, tasks_call

POLICY = ContextPolicy(char_budget=2000, tool_result_budget=100)
USAGE: JsonObject = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}


def build_manager(
    tmp_path: Path, model: Model, search: Search
) -> tuple[SessionManager, RuntimeResources]:
    """真实事件库 + 受控模型与检索 + 投影开启的 multi-agent 阶段。"""
    settings = Settings(
        tmp_path, tmp_path, workflow="multi-agent", knowledge_collection_id="fixture"
    )
    resources = RuntimeResources(settings, retriever=search.for_run)
    workflow = MultiAgentWorkflow(model, retrieval_input, POLICY, max_concurrency=1)
    manager = SessionManager(
        EventStore(tmp_path / "events.sqlite"), workflow, settings, resources
    )
    return manager, resources


async def run_turns(tmp_path: Path, model: Model, messages: list[str]) -> SessionView:
    """单会话多轮提交，返回最终视图。"""
    search = Search()
    manager, resources = build_manager(tmp_path, model, search)
    try:
        sid = (await manager.create_session())["id"]
        for index, message in enumerate(messages):
            await manager.submit_message(sid, f"run{index}", message)
            await manager.tasks[sid]
        return await manager.get_session(sid)
    finally:
        await manager.shutdown()
        resources.close()


def request_messages(model: Model, objective: str) -> list[JsonObject]:
    """取包含某个调查任务目标的请求体；没找到返回空列表。"""
    found: list[JsonObject] = []
    for request in model.requests:
        texts = json.dumps(request["messages"], ensure_ascii=False)
        if objective in texts:
            found.append(request)
    return found


async def test_worker_requests_are_projected_and_isolated(tmp_path: Path) -> None:
    """两个并行文档调查员：各自请求只含自己的任务；非存活工具结果以占位出现（T021）。

    worker 走两轮工具（search → read_document → 报告）：第三轮请求里第一轮的
    search 结果已不属于最后一段交换，按两层机制被占位替换；最后一段交换原样保留。
    """
    model = Model()
    responses: list[ChatCompletion | Exception] = [
        completion(calls=[intent_call("i1", "troubleshoot")], usage=USAGE),
        completion(
            calls=[
                tasks_call(
                    "t1",
                    [
                        {"kind": "docs", "objective": "查明重试策略 objectiveA"},
                        {"kind": "docs", "objective": "查明通知规则 objectiveB"},
                    ],
                )
            ],
            usage=USAGE,
        ),
        completion(calls=[search_call("s1", "重试策略")], usage=USAGE),
        completion(calls=[read_call("r1", "RULE-1")], usage=USAGE),
        completion("调查员 A 的报告。", usage=USAGE),
        completion(calls=[search_call("s2", "通知规则")], usage=USAGE),
        completion(calls=[read_call("r2", "RULE-1")], usage=USAGE),
        completion("调查员 B 的报告。", usage=USAGE),
        completion("最终回答。", usage=USAGE),
    ]
    model.responses = responses
    view = await run_turns(tmp_path, model, ["帮我看看 Webhook 的问题"])
    assert view["status"] == "completed"
    # 每个 worker 的请求只含自己的任务目标，互不污染（Send 并行隔离）。
    requests_a = request_messages(model, "objectiveA")
    requests_b = request_messages(model, "objectiveB")
    assert len(requests_a) == 3 and len(requests_b) == 3
    assert all(
        "objectiveB" not in json.dumps(r["messages"], ensure_ascii=False) for r in requests_a
    )
    assert all(
        "objectiveA" not in json.dumps(r["messages"], ensure_ascii=False) for r in requests_b
    )
    # 投影开启：第三轮请求里，第一轮 search 结果以占位摘要出现；读回结果（存活）原样。
    worker_a_third = json.dumps(requests_a[2]["messages"], ensure_ascii=False)
    assert "placeholder" in worker_a_third
    assert "HTTP 410 后禁用端点" in worker_a_third
    # 九处调用点（route/plan/两 worker×3/synthesize）全部计入本轮用量。
    usage = view["messages"][-1]["usage"]
    assert usage is not None and usage["model_calls"] == 9


async def test_usage_is_per_turn_independent(tmp_path: Path) -> None:
    """两轮会话（文档问答 + 排查）：每轮用量各自汇总，不跨轮累计（T022）。"""
    model = Model()
    responses: list[ChatCompletion | Exception] = [
        completion(calls=[intent_call("i1", "docs")], usage=USAGE),
        completion("按文档回答。", usage=USAGE),
        completion(
            calls=[tasks_call("t1", [{"kind": "docs", "objective": "查明该支付的状态"}])],
            usage=USAGE,
        ),
        completion(calls=[search_call("s1", "支付状态")], usage=USAGE),
        completion("调查员报告。", usage=USAGE),
        completion("最终回答。", usage=USAGE),
    ]
    model.responses = responses
    view = await run_turns(tmp_path, model, ["Webhook 重试策略是什么？", "查一下 pay_bg_001_0003"])
    assert view["status"] == "completed"
    answers = [m for m in view["messages"] if m["role"] == "assistant"]
    assert len(answers) == 2
    first, second = answers[0]["usage"], answers[1]["usage"]
    assert first is not None and first["model_calls"] == 2  # 分类 + 文档问答
    assert second is not None and second["model_calls"] == 4  # 计划 + 调查员×2 + 汇总
