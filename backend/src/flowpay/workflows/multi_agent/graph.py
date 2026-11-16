"""multi-agent 的图定义与阶段入口。

所有节点一律 async def：langgraph 对同步节点走线程池，会把 check-then-act
（claim 的去重记账）切到别的线程，破坏共享 Budget/done 的单事件循环原子性。
runtime（追踪出口、模型调用入口）与 SharedTurn 都是每轮创建的资源，因此图在
每轮 run 时构建，节点以闭包捕获依赖；节点返回 dict 与对应 GraphState 字段
合并，reports 用 operator.add 累加。
"""

# langgraph 的 add_node/compile/ainvoke 重载标注不完整，本文件关闭该检查。
# langgraph.graph 未随分发包提供 stub（或不在类型的搜索路径里），也无法补齐。
# pyright: reportUnknownMemberType=false, reportMissingTypeStubs=false

from __future__ import annotations

from collections.abc import Callable, Hashable
from typing import cast

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send
from openai.types.chat import ChatCompletionMessageParam

from flowpay.core.chat.projection import ContextPolicy
from flowpay.core.chat.record_ids import record_ids
from flowpay.core.chat.usage_view import sum_entries
from flowpay.core.execution.contracts import TurnInput, TurnResult
from flowpay.core.execution.runtime import AgentRuntime
from flowpay.core.execution.stage import StageCard
from flowpay.core.json import JsonObject, to_json_object
from flowpay.core.knowledge.passages import (
    EVIDENCE_MAX_CHARS,
    EVIDENCE_MAX_PASSAGES,
    PassageSet,
)
from flowpay.core.llm.chat import ChatModel
from flowpay.core.memory.contracts import MemoryPolicy
from flowpay.workflows.manual_loop import (
    QUERY_KEYS_CAP,
    Case,
    briefing,
    cap_query_keys,
    conditions_in,
    recall_note_section,
    restore_queried,
)

from .intent import CLASSIFY_PROMPT, DEFAULT_INTENT, SET_INTENT, conditions_route, read_intent
from .investigation import (
    discard_stale,
    doc_worker_node,
    plan_node,
    sql_worker_node,
    synthesize_node,
)
from .simple import chitchat_node, doc_qa_node
from .state import (
    FINDINGS_CAP,
    GraphState,
    NodeOutput,
    SharedTurn,
    merge_old_findings,
    restore_findings,
)


def branch(state: GraphState) -> str:
    """route 之后的条件边：按意图进对应分支。"""
    return state["intent"]


# doc 工具的 finding 记名与 claim 记账名的对应（doc_worker 两处 claim 用短名，
# findings 用工具全名）；不在表中的 tool 名原样使用。
DOC_TOOL_CLAIM = {"search_documents": "search", "read_document": "read"}


def fold_finding_record_ids(shared: SharedTurn) -> None:
    """把上轮结论摘要里的记录编号并入防线集合（specs/002 R-5 的 multi-agent 侧口径）。

    worker 每轮从零开始（remember=False），上轮工具返回的编号只剩 findings 摘要
    可查；不并入的话，本轮回答引用它会被 synthesize 误拦一次（R-5 同源缺口）。
    digest 与 target 来自真实工具返回，assistant 转述不在此列，防线语义不变。
    """
    for finding in shared.findings:
        shared.seen.update(record_ids(finding.digest))
        shared.seen.update(record_ids(finding.target))


def dispatch(state: GraphState) -> list[Send] | str:
    """plan 之后的条件边：每个子任务经 Send 分发给对应调查员并行执行；没有任务直接进汇总。"""
    sends: list[Send] = []
    for task in state["tasks"]:
        payload: GraphState = {
            "user_message": state["user_message"],
            "saved_state": state["saved_state"],
            "intent": state["intent"],
            "tasks": [task],
            "reports": [],
            "answer": "",
            "waiting": False,
        }
        worker = "sql_worker" if task.kind == "sql" else "doc_worker"
        sends.append(Send(worker, payload))
    return sends or "synthesize"


def build_graph(
    model: ChatModel,
    prepare_query: Callable[[str], str],
    runtime: AgentRuntime,
    shared: SharedTurn,
    case_note: str | None,
    context_policy: ContextPolicy | None = None,
):
    """组装轮内编排图：route → plan → Send 并行调查员 → synthesize。"""

    async def route(state: GraphState) -> NodeOutput:
        """意图路由：规则短路命中直接判，未命中交给 LLM 分类，解析失败兜底排查。"""
        if rule := conditions_route(state["user_message"]):
            await runtime.tracer.emit("intent.decided", {"intent": rule, "source": "rule"})
            return {"intent": rule}
        messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": CLASSIFY_PROMPT},
            {"role": "user", "content": state["user_message"]},
        ]
        completion, call_id = await runtime.chat(model).call(
            messages, [SET_INTENT], output="internal"
        )
        shared.record_usage(completion)
        decision = read_intent(completion)
        payload: JsonObject = (
            {"intent": decision.intent, "reason": decision.reason, "source": "model"}
            if decision
            else {"intent": DEFAULT_INTENT, "source": "fallback"}
        )
        await runtime.tracer.emit("intent.decided", payload, call_id)
        return {"intent": payload["intent"]}

    graph = StateGraph(GraphState)
    graph.add_node("route", route)
    graph.add_node("plan", plan_node(model, runtime, shared, case_note, context_policy))
    graph.add_node("sql_worker", sql_worker_node(model, runtime, shared, case_note, context_policy))
    graph.add_node(
        "doc_worker",
        doc_worker_node(model, prepare_query, runtime, shared, case_note, context_policy),
    )
    graph.add_node("synthesize", synthesize_node(model, runtime, shared, case_note, context_policy))
    graph.add_node("doc_qa", doc_qa_node(model, prepare_query, runtime, shared, context_policy))
    graph.add_node("chitchat", chitchat_node(model, runtime, shared))
    graph.add_edge(START, "route")
    graph.add_conditional_edges(
        "route", branch, {"troubleshoot": "plan", "docs": "doc_qa", "chitchat": "chitchat"}
    )
    # Send 未列入 langgraph path 的 Hashable 标注，但条件边返回 Send 列表是官方分发方式。
    graph.add_conditional_edges(
        "plan", cast("Callable[[GraphState], Hashable | list[Hashable]]", dispatch)
    )
    graph.add_edge("sql_worker", "synthesize")
    graph.add_edge("doc_worker", "synthesize")
    graph.add_edge("synthesize", END)
    graph.add_edge("doc_qa", END)
    graph.add_edge("chitchat", END)
    return graph.compile()


class MultiAgentWorkflow:
    """意图路由 + 并行协作排查阶段；Workflow Protocol 的一个实现。"""

    id: str = "flowpay-multi-agent-support"
    version: str = "1"
    card: StageCard = StageCard(
        label="FlowPay 智能支持",
        description="自动识别意图：排查支付与 Webhook 问题、解答文档、日常问候",
        examples=[
            "你好，你能做什么？",
            "Webhook 的重试策略是什么？",
            "有笔支付成功了但一直没收到通知，帮我看看",
        ],
        reply_hint="排查问题会先补齐条件再并行调查；文档问题带引用作答；闲聊不打扰排查状态。",
    )

    def __init__(
        self,
        model: ChatModel,
        prepare_query: Callable[[str], str],
        context_policy: ContextPolicy | None = None,
        memory_policy: MemoryPolicy | None = None,
        max_concurrency: int | None = None,
    ) -> None:
        """接收已装配的模型与查询格式函数；max_concurrency 仅测试用（串行化 Send）。"""
        self.model: ChatModel = model
        self.prepare_query: Callable[[str], str] = prepare_query
        self.context_policy: ContextPolicy | None = context_policy
        self.memory_policy: MemoryPolicy | None = memory_policy
        self.max_concurrency: int | None = max_concurrency

    async def run(self, turn: TurnInput, runtime: AgentRuntime) -> TurnResult:
        """一轮 = 恢复跨轮状态 → 图状态进出 → 组装新检查点。"""
        retrieved = PassageSet.restore(
            turn.saved_state.get("evidence") or [], runtime.collection_id
        )
        # 跨轮携带的证据收敛：淘汰最旧，当轮新增与前端展示不受影响（specs/003 FR-12）。
        retrieved.prune(EVIDENCE_MAX_PASSAGES, EVIDENCE_MAX_CHARS)
        shared = SharedTurn(
            case=Case.restore(turn.saved_state.get("case")),
            done=restore_queried(turn.saved_state.get("queried")),
            findings=restore_findings(turn.saved_state.get("findings")),
            retrieved=retrieved,
        )
        revision = shared.case.apply(conditions_in(turn.user_message))
        discard_stale(shared, revision.changed)
        fold_finding_record_ids(shared)
        # 查询键封顶（specs/004 US7）：当前 findings 引用的键全保留（它们的结论
        # 仍在简报里被引用），其余按序保尾淘汰。seen（编号防线）不动。
        # referenced 必须与 claim 记账键同构：doc 工具的 finding 名与 claim 名不同
        # （search_documents/read_document vs search/read，investigation.py）。
        # merged 合并条目无法还原成员键，其成员键按未引用处理——淘汰的代价只是
        # 该 doc 查询可能重查一次，方向是浪费不是错误。
        referenced: set[str] = set()
        for finding in shared.findings:
            claim_tool = DOC_TOOL_CLAIM.get(finding.tool, finding.tool)
            referenced.add(f"{claim_tool}:{finding.target.casefold()}")
        cap_query_keys(shared.done, referenced, QUERY_KEYS_CAP)
        note = briefing(shared.case, revision)
        # 记忆段并入 case_note：单点注入自动携带到 plan/worker/synthesize（specs/004）。
        section = await recall_note_section(
            turn.user_message, shared.case, runtime, self.memory_policy
        )
        if section:
            note = f"{note}\n\n{section}" if note else section
        # 编造引用核对的材料：用户本轮给出的编号，加上 case 里已有的编号字段值。
        shared.seen.update(record_ids(turn.user_message))
        shared.seen.update(shared.case.known.values())
        state: GraphState = {
            "user_message": turn.user_message,
            "saved_state": turn.saved_state,
            "intent": "",
            "tasks": [],
            "reports": [],
            "answer": "",
            "waiting": False,
        }
        config: RunnableConfig | None = (
            {"max_concurrency": self.max_concurrency} if self.max_concurrency else None
        )
        graph = build_graph(
            self.model, self.prepare_query, runtime, shared, note, self.context_policy
        )
        outcome = await graph.ainvoke(state, config)
        # 跨轮发现封顶：最旧条目合并为摘要条目，改口作废对合并条目仍命中（specs/003 FR-11）。
        shared.findings = merge_old_findings(shared.findings, FINDINGS_CAP)
        checkpoint: JsonObject = {
            **turn.saved_state,
            "case": to_json_object(shared.case),
            "queried": sorted(shared.done),
            "findings": [to_json_object(finding) for finding in shared.findings],
            "evidence": [to_json_object(passage) for passage in shared.retrieved.all()],
            "stop_reason": "waiting_user" if outcome["waiting"] else "answered",
        }
        return TurnResult(
            text=cast("str", outcome["answer"]),
            saved_state=checkpoint,
            evidence=shared.retrieved.all(),
            status="waiting_user" if outcome["waiting"] else "completed",
            usage=sum_entries(shared.usage_entries) if shared.usage_entries else None,
        )
