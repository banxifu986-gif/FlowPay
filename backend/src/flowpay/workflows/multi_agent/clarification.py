"""澄清状态与请求：跨轮记住问过什么、答过什么、明确拿不到什么（specs/007）。

状态随会话检查点持久化，与 Case（查询条件）分离：Case 只放能直接填进工具的
条件，背景主题（目标、现象、时间、版本）放在这里。更新来源是 plan 节点每轮
已有的模型调用，本模块只做状态读写，不额外调用模型。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar, Literal, cast

from pydantic import BaseModel, ConfigDict, Field

from flowpay.core.chat.record_ids import record_ids
from flowpay.core.json import JsonObject, require_list, require_object
from flowpay.core.tools import Tool, ToolInputError
from flowpay.workflows.manual_loop import CONDITION_FIELDS, ConditionField

if TYPE_CHECKING:
    from flowpay.core.knowledge.contracts import Passage

# 背景主题与题库的澄清类别一一对应：goal / symptom_details / occurrence_time /
# api_version。枚举封闭，因此状态条目数天然有界，不需要额外封顶。
ClarificationTopic = Literal["goal", "symptom_details", "occurrence_time", "api_version"]

CLARIFICATION_TOPICS: tuple[ClarificationTopic, ...] = (
    "goal",
    "symptom_details",
    "occurrence_time",
    "api_version",
)

# 条件字段到 benchmark 类别的映射（specs/007 data-model）：账户与环境一类，
# 其余编号一类。类别级去重用它判断“该类是否已经问过”。
CONDITION_CATEGORY: dict[ConditionField, str] = {
    "account_id": "account_context",
    "environment": "account_context",
    "payment_id": "identifiers",
    "request_id": "identifiers",
    "event_id": "identifiers",
    "delivery_id": "identifiers",
    "endpoint_id": "identifiers",
    "customer_id": "identifiers",
}

# 主题值单条上限：与问答正文同量级，防止模型把整段对话回填成状态。
TOPIC_VALUE_CAP = 500


def bounded_value(value: str) -> str:
    """把主题值硬截断到上限；状态是结构化事实，不追加截断标记。"""
    return value[:TOPIC_VALUE_CAP]


def _stored_answers(value: object) -> dict[str, object]:
    """检查点里的 answers 只接受对象；其余形状按空处理。"""
    try:
        return dict(require_object(value or {}))
    except ValueError:
        return {}


def _stored_topics(value: object) -> list[object]:
    """检查点里的主题列表只接受列表；非字符串元素由调用方跳过。"""
    try:
        return list(require_list(value or []))
    except ValueError:
        return []


def known_topics(values: list[object]) -> list[ClarificationTopic]:
    """只保留登记过的主题，重复项去重、顺序按首次出现。"""
    seen: list[ClarificationTopic] = []
    for item in values:
        if not isinstance(item, str):
            continue
        if item in CLARIFICATION_TOPICS and item not in seen:
            seen.append(cast_topic(item))
    return seen


def known_conditions(values: list[object]) -> list[ConditionField]:
    """只保留合法的条件字段，重复项去重、顺序按首次出现。"""
    seen: list[ConditionField] = []
    for item in values:
        if not isinstance(item, str):
            continue
        if item in CONDITION_FIELDS and item not in seen:
            seen.append(item)
    return seen


@dataclass
class ClarificationState:
    """一个会话的澄清进度。

    三组主题互斥地表达"为什么不用再问"：问过了、回答过了、用户明确拿不到。
    answers 与 unavailable_topics 同时出现时以 answers 为准（写入口径由 answer()
    维持，恢复检查点时再按同一口径做一次取舍）。
    """

    asked_topics: list[ClarificationTopic] = field(default_factory=list)
    answers: dict[ClarificationTopic, str] = field(default_factory=dict)
    unavailable_topics: list[ClarificationTopic] = field(default_factory=list)
    # 已问过的条件字段：与 asked_topics 对称的跨轮记忆。主题靠 asked_topics
    # 去重，条件没有对应记录时同一编号会被逐轮反复索要（首轮实测 30 处字段级
    # 重复，同一 request_id 最多 5 轮），因此条件也入状态（SC-3）。
    asked_conditions: list[ConditionField] = field(default_factory=list)

    @classmethod
    def restore(cls, state: object) -> ClarificationState:
        """从检查点恢复；未知主题丢弃、超长值截断、answers 与拿不到冲突时以 answers 为准。

        检查点是跨版本数据，字段结构可能来自旧形状；因此这里按容忍口径读取：
        结构不合法的一律按空或跳过，绝不因为一段坏状态让整轮失败。
        """
        if state is None:
            return cls()
        try:
            stored = require_object(state)
        except ValueError:
            return cls()
        answers: dict[ClarificationTopic, str] = {}
        for name, value in _stored_answers(stored.get("answers")).items():
            if name in CLARIFICATION_TOPICS and isinstance(value, str):
                answers[cast_topic(name)] = bounded_value(value)
        # 先声明拿不到、后又提供值时，以已回答为准（data-model 的恢复规则）。
        unavailable: list[ClarificationTopic] = []
        for topic in known_topics(_stored_topics(stored.get("unavailable_topics"))):
            if topic not in answers:
                unavailable.append(topic)
        return cls(
            asked_topics=known_topics(_stored_topics(stored.get("asked_topics"))),
            answers=answers,
            unavailable_topics=unavailable,
            asked_conditions=known_conditions(_stored_topics(stored.get("asked_conditions"))),
        )

    def answer(self, topic: ClarificationTopic, value: str) -> None:
        """记下用户给出的主题值：改口取最新值，并撤掉"拿不到"标记。"""
        self.answers[topic] = bounded_value(value)
        if topic in self.unavailable_topics:
            self.unavailable_topics.remove(topic)

    def mark_asked(
        self,
        topics: Sequence[ClarificationTopic],
        conditions: Sequence[ConditionField] = (),
    ) -> None:
        """记下本轮已经向用户发问过的主题与条件，避免下一轮重复索要。

        只在 synthesize 最终采纳 clarify 决策后调用一次：worker 提议但模型改判
        作答的问题用户从未看到，不算问过（specs/007 Clarifications）。
        """
        for topic in topics:
            if topic not in self.asked_topics:
                self.asked_topics.append(topic)
        for name in conditions:
            if name not in self.asked_conditions:
                self.asked_conditions.append(name)

    def asked_condition_categories(self) -> set[str]:
        """已问条件覆盖的类别：同类别其它字段不再逐轮换着索要（一次问全）。"""
        return {CONDITION_CATEGORY[name] for name in self.asked_conditions}

    def mark_unavailable(self, topics: list[ClarificationTopic]) -> None:
        """用户明确拿不到的主题：不再追问，旧值也不再算已确认事实。"""
        for topic in topics:
            if topic not in self.unavailable_topics:
                self.unavailable_topics.append(topic)
            self.answers.pop(topic, None)

    def missing_topics(self) -> list[ClarificationTopic]:
        """本轮可以问的主题：没回答、没标记拿不到、也没问过的那些。"""
        return [
            topic
            for topic in CLARIFICATION_TOPICS
            if topic not in self.answers
            and topic not in self.unavailable_topics
            and topic not in self.asked_topics
        ]


def still_missing(
    known: Mapping[ConditionField, str],
    state: ClarificationState,
    pending: Sequence[ClarificationRequest],
    topics: Sequence[ClarificationTopic] = (),
    conditions: Sequence[ConditionField] = (),
) -> tuple[list[ClarificationTopic], list[ConditionField]]:
    """从模型声明的缺项里滤掉"其实已经知道"的部分，返回真正仍缺的主题与条件。

    主题已知即不再问：已回答、用户明说拿不到、已经问过（跨轮 asked_topics 或
    本轮 pending 已含项）都算已知；条件里 Case 已有的与本轮已问过的算已知。
    顺序按调用方给出的次序，重复项只保留第一个。
    """
    pending_topics = {topic for request in pending for topic in request.topics}
    pending_conditions = {name for request in pending for name in request.conditions}
    open_topics: list[ClarificationTopic] = []
    for topic in topics:
        if topic in open_topics:
            continue
        if topic in state.answers or topic in state.unavailable_topics:
            continue
        if topic in state.asked_topics or topic in pending_topics:
            continue
        open_topics.append(topic)
    # 条件的已知口径：用户已给出的、本轮已问过的，加上跨轮已问过的字段与
    # 同类别其它字段——问过该类而用户未提供时，换字段继续钓只会耗尽轮次。
    asked_categories = state.asked_condition_categories()
    open_conditions: list[ConditionField] = []
    for name in conditions:
        if name in open_conditions or name in known or name in pending_conditions:
            continue
        if name in state.asked_conditions or CONDITION_CATEGORY.get(name) in asked_categories:
            continue
        open_conditions.append(name)
    return open_topics, open_conditions


def cast_topic(name: str) -> ClarificationTopic:
    """把检查点里的字符串键收回封闭枚举；调用方已确认它在枚举内。"""
    return cast("ClarificationTopic", name)


def query_with_facts(state: ClarificationState, query: str) -> str:
    """把已回答的主题值拼进检索查询（specs/007 US5 诊断选定的查询改写）。

    离线诊断显示 full（首条消息 + 全部披露事实）比 initial 的 recall@10 高 15.8pp，
    主因是追问得到的事实没有进入检索；这里把同一批事实接到检索查询后面。
    值单条不超过 500 字符、主题枚举封闭，因此拼接长度天然有界。
    """
    facts = [" ".join(value.split()) for value in state.answers.values() if value.strip()]
    return f"{query} {' '.join(facts)}" if facts else query


# 第二跳拼接值的整体上限：沿用既有 ≤500 字符清洗口径（specs/009 FR-10）。
FINDINGS_QUERY_CAP = 500


def query_with_findings(
    findings: Sequence[object], query: str, passages: Sequence[Passage] = ()
) -> str:
    """把本轮第一跳查得的记录编号拼进第二跳检索查询（specs/009 US5）。

    多跳题的第二跳断链在"第一跳查到的记录值没进第二跳查询"；这里从 findings
    的 target/digest 与已取回片段正文提取记录编号，追加到查询后面。findings
    条目按鸭子类型只读 target/digest 两个属性（Finding 的形状），不反向导入
    state（clarification 是 state 的被依赖方，静态 cycle 由引用方向产生）。
    拼接值取有序集合并排序——同一 findings 集合产出同一查询文本，恢复续跑不
    因拼接顺序漂移触发重查（审查 B4）；整体超过 FINDINGS_QUERY_CAP 时截断。
    没有编号时原样返回，行为不变；与 query_with_facts 叠加使用（澄清答案 +
    本轮发现互补）。
    """
    values: set[str] = set()
    for item in findings:
        target = getattr(item, "target", "")
        digest = getattr(item, "digest", "")
        values.update(record_ids(str(target)))
        values.update(record_ids(str(digest)))
    for passage in passages:
        values.update(record_ids(passage.text))
    if not values:
        return query
    suffix = " ".join(sorted(values))
    room = FINDINGS_QUERY_CAP - len(query) - 1
    if room <= 0:
        return query
    suffix = suffix[: max(room, 0)].rstrip()
    return f"{query} {suffix}" if suffix else query


def clarification_note(state: ClarificationState) -> str | None:
    """把澄清进度编成简报段落：已问、已回答、明确拿不到的都不要再索要。

    与 case_note、记忆段一起注入本轮各节点，模型因此知道哪些主题问过了、
    哪些已拿到值、哪些用户明说拿不到——这是"不重复问"的唯一依据。
    没有任何进度时返回 None，不向提示词里塞空段落。
    """
    lines: list[str] = []
    if state.asked_topics:
        lines.append("已问过：" + "、".join(state.asked_topics))
    if state.asked_conditions:
        lines.append("已问过（条件）：" + "、".join(state.asked_conditions))
    if state.answers:
        lines.append(
            "已回答：" + "、".join(f"{topic}={value}" for topic, value in state.answers.items())
        )
    if state.unavailable_topics:
        lines.append("用户明确拿不到：" + "、".join(state.unavailable_topics))
    if not lines:
        return None
    return "\n".join(["澄清进度（这些主题不要再向用户索要）：", *lines])


@dataclass(frozen=True)
class ClarificationRequest:
    """一条待用户回复的追问：缺的条件 + 缺的背景主题 + 面向用户的问题文本。

    conditions 与 topics 都可以为空（只问一类），二者不同时为空由处理函数校验；
    question 保留各调查员的原文，汇总节点据此生成合并后的一条追问。
    """

    conditions: tuple[ConditionField, ...] = ()
    topics: tuple[ClarificationTopic, ...] = ()
    question: str = ""


class ClarifyArguments(BaseModel):
    """模型发起追问时的参数：缺的条件与缺的背景主题，至少一组非空。

    JSON Schema 只把 question 设为必填，跨字段约束（两组不能同时为空）在处理
    函数里校验：模型即使漏了一组，也能收到可纠正的说明而不是协议失败。
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")
    missing: list[ConditionField] = Field(default_factory=list, max_length=len(CONDITION_FIELDS))
    topics: list[ClarificationTopic] = Field(
        default_factory=list, max_length=len(CLARIFICATION_TOPICS)
    )
    question: str = Field(min_length=1, max_length=500, pattern=r"\S")


@dataclass(frozen=True)
class ClarifyOutcome:
    """一次 ask_user 的去向：真正并入本轮追问的缺项，以及回填给模型的说明。"""

    accepted_conditions: tuple[ConditionField, ...] = ()
    accepted_topics: tuple[ClarificationTopic, ...] = ()
    question: str = ""
    notice: str | None = None


class Clarifier:
    """一条追问通道：过滤已经知道的缺项，并把同轮请求合并成一条。

    known 是用户已提供的查询条件（Case.known 的引用），state 是澄清进度，
    pending 是本轮待回复列表。合并的意义是用户只会看到一条追问：第二个
    调查员的缺项并入已有一条，问题文本按各自原文拼接，由汇总节点再组织。
    """

    def __init__(
        self,
        known: Mapping[ConditionField, str],
        state: ClarificationState,
        pending: list[ClarificationRequest],
        bound: Mapping[str, str] | None = None,
    ) -> None:
        """绑定本轮的已知条件、澄清状态、待回复列表与服务端绑定的隔离列。

        bound 是 runtime.scope_overrides 的只读映射：被系统绑定的条件既不来自
        用户、也不该向用户索要，因此在这里按"已知"处理（specs/007 US4）。
        """
        self.known: Mapping[ConditionField, str] = known
        self.bound: Mapping[str, str] = bound or {}
        self.state: ClarificationState = state
        self.pending: list[ClarificationRequest] = pending
        # 本调查员是否已经发出追问：只收束自己，不打断其他调查员继续查。
        self.asked: bool = False

    def accept(self, arguments: JsonObject) -> ClarifyOutcome:
        """校验并收下一次追问；全部被退回时只回填说明，不产生待回复。"""
        try:
            params = ClarifyArguments.model_validate(arguments)
        except ValueError as error:
            raise ToolInputError(str(error)) from error
        if not params.missing and not params.topics:
            raise ToolInputError("追问必须说明缺少的条件或背景主题。")
        asked = self.pending[0] if self.pending else None
        asked_categories = self.state.asked_condition_categories()
        kept_conditions: list[ConditionField] = []
        for name in params.missing:
            if name in self.known or name in self.bound or name in kept_conditions:
                continue
            if asked is not None and name in asked.conditions:
                continue
            # 跨轮已问过的条件与同类别其它字段一并退回：首轮已把该类问全，
            # 用户未提供时换字段继续钓只会重复消耗轮次（SC-3）。
            asked_before = (
                name in self.state.asked_conditions
                or CONDITION_CATEGORY.get(name) in asked_categories
            )
            if asked_before:
                continue
            kept_conditions.append(name)
        kept_topics: list[ClarificationTopic] = []
        for topic in params.topics:
            if topic in kept_topics or topic in self.state.asked_topics:
                continue
            if topic in self.state.answers or topic in self.state.unavailable_topics:
                continue
            if asked is not None and topic in asked.topics:
                continue
            kept_topics.append(topic)
        if not kept_conditions and not kept_topics:
            return ClarifyOutcome(notice=self.refusal(params, asked))
        # 这里不写 asked_topics：用户看到追问才算"问过"，由汇总节点在最终采纳
        # 的决策上写一次（编排写反了会在模型改作答时留下假的问询记录）。
        refused = (
            self.refusal(params, asked) if _dropped(params, kept_conditions, kept_topics) else ""
        )
        self.asked = True
        if asked is None:
            self.pending.append(
                ClarificationRequest(tuple(kept_conditions), tuple(kept_topics), params.question)
            )
            return ClarifyOutcome(
                tuple(kept_conditions),
                tuple(kept_topics),
                params.question,
                _joined("已向用户提出，等待回复。", refused),
            )
        self.pending[0] = ClarificationRequest(
            (*asked.conditions, *kept_conditions),
            (*asked.topics, *kept_topics),
            f"{asked.question}\n\n{params.question}",
        )
        return ClarifyOutcome(
            tuple(kept_conditions),
            tuple(kept_topics),
            params.question,
            _joined("已并入本轮已发出的追问。", refused),
        )

    def refusal(self, arguments: ClarifyArguments, asked: ClarificationRequest | None) -> str:
        """说明这些缺项为什么不再索要：已提供、已问过、已回答或用户说拿不到。"""
        given = [name for name in arguments.missing if name in self.known]
        bound = [name for name in arguments.missing if name in self.bound]
        asked_items = [*(asked.conditions if asked else ()), *(asked.topics if asked else ())]
        asked_categories = self.state.asked_condition_categories()
        asked_before = [
            name
            for name in arguments.missing
            if name not in given
            and name not in bound
            and (
                name in self.state.asked_conditions
                or CONDITION_CATEGORY.get(name) in asked_categories
            )
        ]
        known = [
            topic
            for topic in arguments.topics
            if topic in self.state.answers
            or topic in self.state.unavailable_topics
            or topic in self.state.asked_topics
        ]
        reasons = [
            f"用户已经提供：{'、'.join(given)}" if given else "",
            f"这些条件已由系统绑定，不要索要：{'、'.join(bound)}" if bound else "",
            f"本轮已问过：{'、'.join(asked_items)}" if asked_items else "",
            (f"这些条件已问过（用户未提供，请基于已有信息说明限制）：{'、'.join(asked_before)}")
            if asked_before
            else "",
            f"这些主题不要再问：{'、'.join(known)}" if known else "",
        ]
        detail = "；".join(reason for reason in reasons if reason)
        return f"{detail}。请直接使用已知信息继续排查。"


def _dropped(
    arguments: ClarifyArguments,
    kept_conditions: list[ConditionField],
    kept_topics: list[ClarificationTopic],
) -> bool:
    """这次请求里是否有任何一项被退回（已知、已问过、系统绑定或重复）。"""
    return len(kept_conditions) < len(set(arguments.missing)) or len(kept_topics) < len(
        set(arguments.topics)
    )


def _joined(head: str, refused: str) -> str:
    """把"已发出"的结果与被退回项的说明接成一段回填文本。"""
    return f"{head} {refused}" if refused else head


ClarifyHandler = Callable[[JsonObject], ClarifyOutcome]


def clarify_tool(handler: ClarifyHandler) -> Tool:
    """把追问通道包成模型可见工具 ask_user；只做协议校验与序列化。"""

    async def ask(arguments: JsonObject) -> JsonObject:
        """把协议参数交给处理函数，结果原样回填，让模型知道追问是否真的发出。"""
        outcome = handler(arguments)
        payload: JsonObject = {
            "missing": list(outcome.accepted_conditions),
            "topics": list(outcome.accepted_topics),
            "question": outcome.question,
        }
        if outcome.notice:
            payload["notice"] = outcome.notice
        return payload

    return Tool(
        name="ask_user",
        description=(
            "向用户索要发起查询所必需的条件（missing）与排查背景（topics：目标、现象、"
            "发生时间、API 版本）。先执行当前条件能执行的查询，只有缺必需条件或证据不足时"
            "才调用；一次把所有缺项问全，已知的、本轮已问过的、用户说拿不到的不要重复问。"
        ),
        parameters={
            **ClarifyArguments.model_json_schema(),
            "additionalProperties": False,
        },
        execute=ask,
    )
