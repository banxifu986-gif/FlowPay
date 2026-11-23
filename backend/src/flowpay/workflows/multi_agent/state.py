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

from openai.types.chat import ChatCompletion

from flowpay.core.agent import Budget
from flowpay.core.chat.projection import clip_text
from flowpay.core.chat.usage_view import UsageEntry, from_completion
from flowpay.core.json import JsonObject, require_int, require_list, require_object, require_text
from flowpay.core.knowledge.passages import PassageSet
from flowpay.workflows.manual_loop import Case, ToolAction

from .clarification import ClarificationRequest, ClarificationState

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


# 跨轮发现清单的上限与合并条目的摘要预算（specs/003 P2，模块常量不进配置面）。
FINDINGS_CAP = 20
MERGED_DIGEST_CAP = 2000


def merge_old_findings(findings: list[Finding], cap: int) -> list[Finding]:
    """跨轮发现超过上限时把最旧的若干条合并为一条摘要条目（specs/003 FR-11）。

    合并条目 tool=merged、target 为成员以「；」拼接——discard_stale 的子串匹配
    语义因此保持（改口作废对合并条目仍命中）、rows 求和、digest 拼接后截断到
    MERGED_DIGEST_CAP。不超上限时原样返回新列表，不做任何合并。
    """
    if len(findings) <= cap:
        return list(findings)
    drop = len(findings) - cap + 1
    merged_batch = findings[:drop]
    merged = Finding(
        "merged",
        "；".join(f.target for f in merged_batch),
        sum(f.rows for f in merged_batch),
        clip_text("；".join(f.digest for f in merged_batch), MERGED_DIGEST_CAP),
    )
    return [merged, *findings[drop:]]


@dataclass
class SharedTurn:
    """本轮共享资源。

    case/done/findings/retrieved 从 saved_state 恢复，轮末组装回检查点；
    budget 全体共享一份（Budget(16, 12)），由 claim 统一记账工具调用。
    pending 非空时 synthesize 跳过合成、本轮以 waiting_user 结束——追问是正常返回，
    不是异常控制流；发起追问的调查员自己收束，其余调查员继续把能查的查完。
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
    pending: list[ClarificationRequest] = field(default_factory=list)
    # 澄清进度：问过/答过/明确拿不到的主题，与 Case 的查询条件分开记（specs/007）。
    clarification: ClarificationState = field(default_factory=ClarificationState)
    # 本轮全部模型调用的用量：worker 由 investigate 收尾并入，直连节点在调用点登记。
    usage_entries: list[UsageEntry] = field(default_factory=list)
    # FR-2 工具链路 run 内禁用登记（specs/009）：元素为链路标识（如 embedding、
    # sql:<source>、logs），由工具包装层写入；轮次终止随 SharedTurn 一起销毁，
    # 不跨轮、不跨会话。
    tool_outages: set[str] = field(default_factory=set)
    # FR-4 子任务断点：worker 收尾登记增量进度，恢复由 dispatch 按幂等键过滤。
    # 不写检查点——断点只经由 turn.progress 事件与恢复注入流转（specs/009）。
    task_progress: dict[str, SubtaskProgress] = field(default_factory=dict)
    # FR-9 单任务工具调用计数：键同 task_progress 的幂等键，轮内独立记账。
    task_tool_calls: dict[str, int] = field(default_factory=dict)
    # FR-9 单任务工具调用上限：claim 双记账消费、investigate 循环达限收束
    # （specs/009 US4）；0 表示不启用。默认同 settings 默认值 16。
    investigation_tool_budget: int = 16

    def record_usage(self, completion: ChatCompletion) -> None:
        """登记一次直连模型调用的用量；不经 AgentTurn 的节点（route/plan/直答）使用。"""
        self.usage_entries.append(from_completion(completion))


@dataclass(frozen=True)
class SubtaskProgress:
    """单个 worker 子任务的断点状态（specs/009 data-model SubtaskProgress）。

    done_keys/findings/evidence_refs 是收尾时刻的共享快照：查询记账与发现池
    由并行调查员共用，子任务无法独占归属，恢复时按并集合回 SharedTurn。
    """

    key: str
    worker: str
    objective: str
    done_keys: list[str]
    findings: list[str]
    evidence_refs: list[str]
    finished: bool

    @classmethod
    def restore(cls, raw: object) -> SubtaskProgress | None:
        """从 turn.progress 载荷重建；结构不合法返回 None，不让坏事件打断续跑。"""
        try:
            record = require_object(raw)
            return cls(
                require_text(record["key"]),
                require_text(record["worker"]),
                require_text(record["objective"]),
                [require_text(item) for item in require_list(record["done_keys"])],
                [require_text(item) for item in require_list(record["findings"])],
                [require_text(item) for item in require_list(record["evidence_refs"])],
                bool(record["finished"]),
            )
        except (KeyError, TypeError, ValueError):
            return None


def task_key(worker: str, objective: str) -> str:
    """幂等键 = worker 类型 + 任务目标；落盘与恢复用同一构造（specs/009 data-model）。"""
    return f"{worker}:{objective.casefold().strip()}"
