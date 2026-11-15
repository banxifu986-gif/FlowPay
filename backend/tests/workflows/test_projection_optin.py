"""两阶段 opt-in 的投影行为（T020-T022）：worker 请求含占位、Send 隔离、轮间用量独立。

全部走 multi-agent 的文档路径（无业务数据源依赖）；Send 以 max_concurrency=1
串行化来固定受控模型的 FIFO 响应顺序，隔离断言关注请求内容而非时序。
直连护栏用例（specs/003 T011）：policy=None 时五节点拼接逐字一致；开启后
简报/报告/检索片段截断带标记、question 原样。
"""

import json
from dataclasses import replace as data_replace
from pathlib import Path

from openai.types.chat import ChatCompletion

from flowpay.core.agent.trace import Tracer
from flowpay.core.chat.projection import ContextPolicy
from flowpay.core.chat.types import SessionView
from flowpay.core.config import Settings
from flowpay.core.event_log import EventStore
from flowpay.core.execution.contracts import TurnInput
from flowpay.core.execution.manager import SessionManager
from flowpay.core.execution.resources import RuntimeResources
from flowpay.core.execution.runtime import AgentRuntime
from flowpay.core.json import JsonObject, require_list, require_object
from flowpay.core.knowledge.contracts import Passage
from flowpay.core.knowledge.passages import PassageSet
from flowpay.core.llm.embedding.ollama import retrieval_input
from flowpay.workflows.multi_agent import MultiAgentWorkflow

from .fixtures import (
    Model,
    Search,
    completion,
    hit,
    intent_call,
    read_call,
    search_call,
    tasks_call,
)

POLICY = ContextPolicy(char_budget=2000, tool_result_budget=100)
# 直连护栏（specs/003）：整体预算收紧以驱动简报/片段截断；报告读取端上限为常量 4096。
GUARD = ContextPolicy(char_budget=2000, tool_result_budget=100)
USAGE: JsonObject = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}


def build_manager(
    tmp_path: Path,
    model: Model,
    search: Search,
    policy: ContextPolicy | None = POLICY,
) -> tuple[SessionManager, RuntimeResources]:
    """真实事件库 + 受控模型与检索 + 指定策略的 multi-agent 阶段。"""
    settings = Settings(
        tmp_path, tmp_path, workflow="multi-agent", knowledge_collection_id="fixture"
    )
    resources = RuntimeResources(settings, retriever=search.for_run)
    workflow = MultiAgentWorkflow(model, retrieval_input, policy, max_concurrency=1)
    manager = SessionManager(
        EventStore(tmp_path / "events.sqlite"), workflow, settings, resources
    )
    return manager, resources


async def run_turns(
    tmp_path: Path,
    model: Model,
    messages: list[str],
    policy: ContextPolicy | None = POLICY,
    search: Search | None = None,
) -> SessionView:
    """单会话多轮提交，返回最终视图。"""
    search = search or Search()
    manager, resources = build_manager(tmp_path, model, search, policy)
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


def long_hit(identifier: str, text: str) -> Passage:
    """超长正文片段：驱动 doc_qa 检索片段注入的体积裁剪路径。"""
    return data_replace(hit(), id=identifier, text=text)


def user_content(model: Model, marker: str) -> str:
    """取请求里含指定标记的 user 消息正文；没找到返回空串。"""
    for request in model.requests:
        for message in require_list(request["messages"]):
            item = require_object(message)
            if item.get("role") == "user" and marker in str(item.get("content")):
                return str(item.get("content"))
    return ""


def plan_request_of(model: Model) -> JsonObject:
    """取声明了 set_tasks 的请求体（plan 节点独有）。"""
    for request in model.requests:
        for tool in require_list(request["tools"]):
            function = require_object(require_object(tool).get("function"))
            if function.get("name") == "set_tasks":
                return request
    raise AssertionError("no request declares set_tasks")


def plan_question(model: Model) -> str:
    """plan 请求的 user 正文。"""
    message = require_object(require_list(plan_request_of(model)["messages"])[1])
    return str(message.get("content"))


async def run_troubleshoot(
    tmp_path: Path,
    model: Model,
    search: Search,
    policy: ContextPolicy | None,
    question: str,
    saved_state: JsonObject | None = None,
) -> None:
    """直连 MultiAgentWorkflow 跑一轮排查（不经 SessionManager，便于注入 saved_state）。"""
    settings = Settings(
        tmp_path, tmp_path, workflow="multi-agent", knowledge_collection_id="fixture"
    )
    resources = RuntimeResources(settings, retriever=search.for_run)
    tracer = Tracer(EventStore(tmp_path / "events.sqlite"), "s1", "r1")
    runtime = AgentRuntime(
        tracer, resources.source_client, resources.document_retriever(tracer), "fixture"
    )
    workflow = MultiAgentWorkflow(model, retrieval_input, policy, max_concurrency=1)
    try:
        await workflow.run(TurnInput(question, [], saved_state or {}), runtime)
    finally:
        resources.close()


async def test_light_nodes_untouched_without_policy(tmp_path: Path) -> None:
    """policy=None：doc_qa 与 synthesize 的拼接与原格式逐字一致（US2 回归契约）。"""
    model = Model()
    model.responses = [
        completion(calls=[intent_call("i1", "docs")], usage=USAGE),
        completion("按文档回答。", usage=USAGE),
    ]
    question = "Webhook 重试策略是什么？"
    await run_turns(tmp_path, model, [question], policy=None)
    expected = PassageSet()
    expected.add([hit()])
    content = user_content(model, "以下是知识库检索到的片段")
    assert content == (
        f"{question}\n\n以下是知识库检索到的片段，编号用于引用：\n{expected.serialize()}"
    )

    model = Model()
    model.responses = [
        completion(calls=[intent_call("i1", "troubleshoot")], usage=USAGE),
        completion(
            calls=[tasks_call("t1", [{"kind": "docs", "objective": "查明该支付的状态"}])],
            usage=USAGE,
        ),
        completion(calls=[search_call("s1", "支付状态")], usage=USAGE),
        completion(calls=[read_call("r1", "RULE-1")], usage=USAGE),
        completion("调查员 A 的报告。", usage=USAGE),
        completion("最终回答。", usage=USAGE),
    ]
    await run_troubleshoot(tmp_path, model, Search(), None, question)
    # plan 时 findings 尚空，user 正文就是裸问题。
    assert plan_question(model) == question
    # synthesize 的拼接与原格式逐字一致（简报含本轮 worker 已产生的 findings）。
    assert user_content(model, "调查报告：") == (
        f"{question}\n\n"
        "本会话已执行的查询与结论（相同查询不要重复执行，直接引用结论）：\n"
        "- search_documents 支付状态：1 个片段；首片 doc:RULE-1#c001\n"
        "- read_document RULE-1：1 个片段；首片 doc:RULE-1#c001\n\n"
        "调查报告：\n[docs 调查员]\n调查员 A 的报告。"
    )


async def test_doc_qa_passages_clipped_with_policy(tmp_path: Path) -> None:
    """policy 开启：超长检索片段被截断带标记，question 原样（US2）。"""
    model = Model()
    model.responses = [
        completion(calls=[intent_call("i1", "docs")], usage=USAGE),
        completion("按文档回答。", usage=USAGE),
    ]
    search = Search()
    search.search_results = [long_hit("doc:LONG#c001", "长" * 3000)]
    question = "Webhook 重试策略是什么？"
    await run_turns(tmp_path, model, [question], policy=GUARD, search=search)
    content = user_content(model, "以下是知识库检索到的片段")
    assert content.startswith(f"{question}\n\n以下是知识库检索到的片段，编号用于引用：\n")
    assert "已截断" in content and "重查" in content
    assert "长" * 3000 not in content  # 超预算部分被截断
    assert "长" * 1800 in content  # 预算内正文保留（整体预算 2000 减去标记开销）


async def test_plan_and_synthesize_clipped_with_policy(tmp_path: Path) -> None:
    """policy 开启：长简报进 plan/synthesize 均被裁剪，长报告在 synthesize 读取端截断。"""
    model = Model()
    model.responses = [
        completion(calls=[intent_call("i1", "troubleshoot")], usage=USAGE),
        completion(
            calls=[tasks_call("t1", [{"kind": "docs", "objective": "查明该支付的状态"}])],
            usage=USAGE,
        ),
        completion(calls=[search_call("s1", "支付状态")], usage=USAGE),
        completion(calls=[read_call("r1", "RULE-1")], usage=USAGE),
        completion("长" * 25000, usage=USAGE),
        completion("最终回答。", usage=USAGE),
    ]
    question = "查一下这笔支付"
    long_digest = "长" * 5000
    saved_state: JsonObject = {
        "findings": [
            {
                "tool": "sql",
                "target": "payments account=acct_bg_001",
                "rows": 1,
                "digest": long_digest,
            }
        ]
    }
    await run_troubleshoot(tmp_path, model, Search(), GUARD, question, saved_state)
    plan_content = plan_question(model)
    assert plan_content.startswith(question + "\n\n")
    assert "已截断" in plan_content  # 简报超整体预算被裁剪
    assert "长" * 5000 not in plan_content
    synthesize_content = user_content(model, "调查报告：")
    assert synthesize_content.startswith(question + "\n\n")
    assert "已截断" in synthesize_content  # 报告 25000 超剩余分配读取端预算
    assert "长" * 25000 not in synthesize_content


async def test_briefing_survives_when_reports_exhaust_budget(tmp_path: Path) -> None:
    """报告把均分份额吃满、简报预算算尽时：简报退化为原文而非标记湮灭（审核【1】）。"""
    model = Model()
    model.responses = [
        completion(calls=[intent_call("i1", "troubleshoot")], usage=USAGE),
        completion(
            calls=[
                tasks_call(
                    "t1",
                    [
                        {"kind": "docs", "objective": "查明重试策略A"},
                        {"kind": "docs", "objective": "查明重试策略B"},
                    ],
                )
            ],
            usage=USAGE,
        ),
        completion(calls=[search_call("s1", "重试A")], usage=USAGE),
        completion(calls=[read_call("r1", "RULE-1")], usage=USAGE),
        completion("长" * 6000, usage=USAGE),
        completion(calls=[search_call("s2", "重试B")], usage=USAGE),
        completion(calls=[read_call("r2", "RULE-1")], usage=USAGE),
        completion("长" * 6000, usage=USAGE),
        completion("最终回答。", usage=USAGE),
    ]
    tight = ContextPolicy(char_budget=8000, tool_result_budget=100)
    question = "查一下这笔支付"
    saved_state: JsonObject = {
        "findings": [
            {"tool": "sql", "target": "payments account=acct_bg_001", "rows": 1, "digest": "3 行"}
        ]
    }
    await run_troubleshoot(tmp_path, model, Search(), tight, question, saved_state)
    synthesize_content = user_content(model, "调查报告：")
    # 简报原文存活（非标记湮灭），报告照常截断。
    assert "本会话已执行的查询与结论" in synthesize_content
    assert "acct_bg_001" in synthesize_content
    assert "已截断" in synthesize_content  # 报告超均分份额被截
