"""图状态与共享轮次资源。

GraphState 是节点间传递的数据，只承载节点产出；SharedTurn 是本轮全体 Agent
共享的可变资源——预算、去重、条件都是天然单例，放进图状态既无法用 reducer
表达共享语义，也会把非 JSON 对象带进状态。每轮由 MultiAgentWorkflow 构建一次，
以闭包注入各节点；节点只原地修改字段，不替换引用。
"""

from __future__ import annotations

import operator
from collections.abc import Awaitable
from dataclasses import dataclass, field
from typing import Annotated, Literal, Protocol, TypedDict

from flowpay.core.agent import Budget
from flowpay.core.json import JsonObject, require_int, require_list, require_object, require_text
from flowpay.core.knowledge.passages import PassageSet
from flowpay.workflows.manual_loop import AskUserResult, Case, ToolAction

# 节点的统一签名：读图状态，返回要合并回去的部分状态更新。
# runtime/shared 是每轮资源，节点以闭包捕获依赖，因此包内节点由工厂函数构造。
NodeOutput = dict[str, object]


class Node(Protocol):
    """图节点协议：langgraph 的节点协议要求参数可按关键字传入，Callable 别名匹配不上。"""

    def __call__(self, state: GraphState) -> Awaitable[NodeOutput]: ...


class GraphState(TypedDict):
    """节点间传递的图状态；字段语义见 specs/001 data-model.md。"""

    user_message: str
    saved_state: JsonObject
    intent: str
    tasks: list[InvestigationTask]
    reports: Annotated[list[WorkerReport], operator.add]
    answer: str
    waiting: bool


@dataclass(frozen=True)
class InvestigationTask:
    """协调者拆解出的一个调查子任务；kind 决定 Send 分发给哪个调查员。"""

    kind: Literal["sql", "docs"]
    objective: str


@dataclass(frozen=True)
class WorkerReport:
    """单个调查员的结论摘要；synthesize 合并所有报告后作答。"""

    worker: Literal["sql", "docs"]
    text: str


@dataclass(frozen=True)
class Finding:
    """一次查询的跨轮结论摘要。

    multi-agent 的调查员每轮从头开始（remember=False），上一轮的工具原始返回
    不在上下文里；靠这份摘要，"上一轮查过、结论是什么"才能注入本轮 briefing，
    去重拦截重复查询时模型也知道该去哪里找已有结论。
    """

    tool: str
    target: str
    rows: int
    digest: str


def restore_findings(state: object) -> list[Finding]:
    """恢复此前轮次的查询结论摘要；没有检查点时从空开始。"""
    if state is None:
        return []
    findings: list[Finding] = []
    for item in require_list(state):
        record = require_object(item)
        findings.append(
            Finding(
                require_text(record["tool"]),
                require_text(record["target"]),
                require_int(record["rows"]),
                require_text(record["digest"]),
            )
        )
    return findings


@dataclass
class SharedTurn:
    """本轮共享资源。

    case/done/findings/retrieved 从 saved_state 恢复，轮末组装回检查点；
    budget 全体共享一份（Budget(16, 12)），由 claim 统一记账工具调用。
    pending 置位后，worker 循环短路、synthesize 跳过合成，本轮以
    waiting_user 结束——追问是正常返回，不是异常控制流。
    """

    case: Case = field(default_factory=Case)
    done: set[str] = field(default_factory=set)
    findings: list[Finding] = field(default_factory=list)
    # 出现过即可引用的记录编号：用户消息与 case 给出的，加上调查员工具实际返回的。
    # 汇总节点据此核对最终回答里的编号，堵住"报告转述时编造记录"的缺口（SC-4）。
    seen: set[str] = field(default_factory=set)
    retrieved: PassageSet = field(default_factory=PassageSet)
    budget: Budget = field(default_factory=lambda: Budget(16, 12))
    actions: list[ToolAction] = field(default_factory=list)
    pending: list[AskUserResult] = field(default_factory=list)
