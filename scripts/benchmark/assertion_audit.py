#!/usr/bin/env python3
"""010 逐断言校验的旁路回放驱动：从验收臂事件库恢复草稿与证据，重放确定性
校验链后执行校验器，产出逐题逐断言判定 JSONL（specs/010 US2/T009）。

不重跑 agent（FR-8）：草稿自 synthesize 决策调用的 model.response 载荷恢复
（retrieval.py 按 seq 读事件先例），本轮证据自 run.finished 检查点的
state.evidence 恢复（与在线 PassageSet 序列化同构），记录摘录自 tool.result
恢复。校验器对桩开发（树A 合并前）：默认 FixtureVerifier 本地词面判定零 API
费用，--fixture 注入判定覆写，--verifier 注入真实 core 校验器（T018 真联调）。

同构口径：回放恢复的是原始草稿（最终采纳的那次决策调用），重放与在线
audited_response 相同的引用标记校验得到 gate 输入（research D6）——不直接
校验发布文本，保证回放结论可迁移到在线档。编造核对的 DISPUTE 重试链不重放：
事件库中的最后一次成功决策已是重试终态，回放改为校验终态草稿编号 ⊆ 轮内
已见编号，不符时记 deviation（诚实暴露终态假设失效，不静默改写）。
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import sqlite3
import sys
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Protocol, cast

from flowpay.core.chat.record_ids import record_ids
from flowpay.core.event_log.types import Event
from flowpay.core.json import (
    JsonObject,
    parse_json,
    require_int,
    require_list,
    require_object,
    require_text,
)
from flowpay.core.knowledge.contracts import Passage
from flowpay.core.knowledge.passages import PassageSet
from flowpay.core.llm.chat.client import ChatClient
from flowpay.core.llm.chat.contracts import ChatModel
from flowpay.core.llm.chat.openai_chat import OpenAiChatModel
from flowpay.core.llm.chat.openai_responses import OpenAiResponsesModel
from flowpay.core.verify import (
    Assertion,
    AssertionVerdict,
    Conflict,
    Overreach,
    OverreachDimension,
    Verdict,
)
from flowpay.dataset import require_dataset


class _NullSink:
    """回放侧空事件汇：真实校验器的模型调用不落事件（回放零写入），调用面补齐。"""

    async def emit(
        self,
        kind: str,
        payload: JsonObject,
        call_id: str | None = None,
        parent_call_id: str | None = None,
    ) -> Event:
        del call_id, parent_call_id
        return Event(
            schema_version=1,
            event_id="",
            session_id="",
            seq=0,
            run_id=None,
            call_id=None,
            parent_call_id=None,
            timestamp="",
            type=kind,
            payload=payload,
        )


class CoreVerifier:
    """T018 真联调：真实 core 校验器的无参工厂（--verifier 注入）。

    复用运营模型配置（bootstrap 先例：responses/chat 协议分流），证据预算取
    配置默认；判定走 core/verify.errors.audit_response（fail-open 在 core 层
    兜底——异常时返回空判定，回放侧行如实呈现）。回放不计运营用量
    （usage_entries 不传），模型调用经 _NullSink 不落事件。
    """

    def __init__(self) -> None:
        from flowpay.core.config import load_settings

        settings = load_settings()
        chat_model: ChatModel = (
            OpenAiResponsesModel(settings.model)
            if settings.model.protocol == "responses"
            else OpenAiChatModel(settings.model)
        )
        self._chat: ChatClient = ChatClient(chat_model, _NullSink())
        self._budget: int = settings.assertion_audit_evidence_budget

    async def audit(self, request: AuditInput) -> tuple[AssertionVerdict, ...]:
        from flowpay.core.verify.errors import audit_response

        audit = await audit_response(
            self._chat,
            text=request.text,
            question=request.question,
            passages=request.passages,
            record_excerpts=request.records,
            evidence_budget=self._budget,
        )
        return audit.verdicts


def core_verifier() -> CoreVerifier:
    """--verifier scripts.benchmark.assertion_audit:core_verifier 的注入入口。"""
    return CoreVerifier()

ROOT = Path(__file__).resolve().parents[2]
# 同时支持 python scripts/benchmark/assertion_audit.py 与
# python -m scripts.benchmark.assertion_audit。
if __package__ in {None, ""}:
    sys.path.insert(0, str(ROOT))

from flowpay.workflows.multi_agent.citation_check import (
    PASSAGE_REF,
    check_citations,
    has_citation,
    is_assertion,
    split_sentences,
)

from scripts.benchmark import evidence
from scripts.benchmark.attribution import Row, load_rows

# run.py 顶层带 event_log.runtime 的 fcntl 依赖（容器-only），宿主回放不能
# import 它；题库加载与抽样按 run.py 的实现本地化（normalize 已有 retrieval.py
# 本地化先例），语义注释逐条对应，改动需同步两处。
TYPES = (
    "simple_retrieval",
    "multi_document",
    "multi_hop",
    "temporal_versioned",
    "multi_source",
    "tool_use_diagnostic",
)


def blueprints() -> list[JsonObject]:
    """读取 300 题；不区分 dev/test，与 run.blueprints 同口径。"""
    source = require_dataset() / "benchmark/design/benchmark_blueprints.jsonl"
    lines = source.read_text(encoding="utf-8").splitlines()
    return [require_object(parse_json(line)) for line in lines]


def sample(rows: list[JsonObject], per_type: int) -> list[JsonObject]:
    """每种题型取前若干道，保证六类都有；per_type 为 0 表示全量。"""
    if not per_type:
        return rows
    picked: list[JsonObject] = []
    for kind in TYPES:
        matching = [r for r in rows if require_text(r["question_type"]) == kind]
        picked.extend(matching[:per_type])
    return picked


def normalize(text: str) -> str:
    """空白归一化：把连续空白折成单个空格，去掉首尾空白。"""
    return " ".join(text.split())


# 判定档的中文展示（报告用）；英文档名保持与 verdict-schema 一致。
VERDICT_LABELS: dict[str, str] = {
    "supported": "支持",
    "partial": "部分支持",
    "contradicted": "矛盾",
    "insufficient": "证据不足",
}

# 与在线 PassageSet 相同的入口路径：检查点 state.evidence 的恢复键。
EVIDENCE_KEY = "evidence"

# 文档工具的片段走 passages 恢复，其余 tool.result 都是记录数据来源（R96B2 实测
# 有 query_payments/get_api_request/list_incidents 等 8 种）；按反集口径不枚举
# sql 工具名，新数据工具加入时摘录口径自动覆盖，与 evidence.observed 的
# records 口径（全量 identifiers_in 扫描）一致。
DOC_TOOL_NAMES = frozenset({"search_documents", "read_document"})

# 单条记录摘录的字符封顶：摘录供判定的语境定位，不是原文回放。
RECORD_EXCERPT_CAP = 2000


def optional_text(record: JsonObject, key: str) -> str | None:
    """可选字符串字段：缺失保持 None，出现即要求是文本。"""
    value = record.get(key)
    return None if value is None else require_text(value)


def read_session_events(arm: Path) -> dict[str, list[Event]]:
    """读臂会话库：按 session 分组的完整事件序列，组内按 seq 升序。

    retrieval.read_sessions 先例的全文版：草稿（model.request/response）、
    证据（run.finished 检查点）与记录摘录（tool.result）分布在全类型事件里，
    这里一次读完，按会话首现顺序返回。
    """
    path = arm / "multi-agent" / "sessions.sqlite"
    if not path.exists():
        raise ValueError(f"no session store under {arm}")
    with sqlite3.connect(path) as db:
        rows = cast(
            "list[tuple[str, str, str]]",
            db.execute(
                "SELECT session_id, type, data FROM events ORDER BY seq"
            ).fetchall(),
        )
    sessions: dict[str, list[Event]] = {}
    for session_id, kind, data in rows:
        record = require_object(parse_json(require_text(data)))
        item: Event = {
            "schema_version": require_int(record.get("schema_version", 1)),
            "event_id": require_text(record.get("event_id", "")),
            "session_id": session_id,
            "seq": require_int(record.get("seq", 0)),
            "run_id": optional_text(record, "run_id"),
            "call_id": optional_text(record, "call_id"),
            "parent_call_id": optional_text(record, "parent_call_id"),
            "timestamp": require_text(record.get("timestamp", "")),
            "type": kind,
            "payload": require_object(record["payload"]),
        }
        sessions.setdefault(session_id, []).append(item)
    return sessions


def question_of(case: JsonObject) -> str:
    """题的首条用户消息（评测臂的会话锚点）：多轮题取 initial_user_message。"""
    conversation = require_object(case["conversation"])
    return require_text(conversation["initial_user_message"])


def match_sessions(
    cases: Sequence[JsonObject], sessions: Mapping[str, list[Event]]
) -> dict[str, str]:
    """把题匹配到会话：首条用户消息按空白归一后与 initial_user_message 相等。

    retrieval.observed_queries 先例：匹配不到的会话不猜归属；同一文本重复出现
    时保留第一个会话（评测臂每题一会话，重复文本是数据缺陷不是歧义）。
    """
    matchers = {
        normalize(question_of(case)): require_text(case["question_id"])
        for case in cases
    }
    matched: dict[str, str] = {}
    for session_id, events in sessions.items():
        first = next((e for e in events if e["type"] == "message.user"), None)
        if first is None:
            continue
        content = require_text(require_object(first["payload"])["content"])
        question_id = matchers.get(normalize(content))
        if question_id is not None and question_id not in matched:
            matched[question_id] = session_id
    return matched


def draft_of(events: Sequence[Event]) -> tuple[str | None, str]:
    """恢复 synthesize 草稿与来源分支：最后一次成功决策调用的正文。

    决策调用 = model.response 的首个 tool_call 是 set_response（synthesize_node
    只带这一个工具，read_response 只读首个 tool_call）。DISPUTE/OUTPUT_RETRY
    重试链的终态就是最后一次成功决策，恢复它即隐含重试结果（research D6）。

    返回 (草稿, 分支)：分支 ∈ {synthesize_answer, clarify_terminal, light_branch}。
    轻分支（doc_qa/chitchat）不经 synthesize 统一出口，不在校验范围（spec 拍板
    "单 agent 阶段不校验"），草稿为 None；clarify 终态轮无发布作答，草稿为 None。
    """
    for event in reversed(events):
        if event["type"] != "model.response":
            continue
        completion = require_object(require_object(event["payload"])["completion"])
        choices = require_list(completion["choices"])
        message = require_object(require_object(choices[0])["message"])
        calls = message.get("tool_calls")
        if not calls:
            continue
        call = require_object(require_list(calls)[0])
        function = require_object(call.get("function") or {})
        if require_text(function.get("name", "")) != "set_response":
            continue
        arguments = require_object(parse_json(require_text(function["arguments"])))
        if require_text(arguments.get("mode", "")) != "answer":
            return None, "clarify_terminal"
        return require_text(arguments["answer"]), "synthesize_answer"
    return None, "light_branch"


def passages_of(events: Sequence[Event]) -> PassageSet:
    """恢复本轮证据池：最后一次 run.finished 检查点的 state.evidence。

    与在线 MultiAgentWorkflow 的 PassageSet.restore 同构：跨轮 prune 后的
    最终池，片段 id/locator/原文齐全（FR-1 全轮片段的来源）。collection_id
    自片段自带溯源恢复——回放不预先绑定集合，restore 的版本防线按原样生效。
    """
    finished = [event for event in events if event["type"] == "run.finished"]
    if not finished:
        return PassageSet()
    state = require_object(require_object(finished[-1]["payload"]).get("state") or {})
    entries = require_list(state.get(EVIDENCE_KEY) or [])
    collection_id = None
    if entries:
        source = require_object(require_object(entries[0]).get("source") or {})
        value = source.get("collection_id")
        collection_id = require_text(value) if value is not None else None
    return PassageSet.restore(entries, collection_id)


def record_excerpts_of(events: Sequence[Event]) -> dict[str, str]:
    """恢复记录摘录：非文档 tool.result 的返回 JSON，按出现的记录编号索引。

    evidence.observed 的 records 口径（identifiers_in 全量扫描）；同一编号多源
    出现时保留先到的（评测臂工具按序执行，先到的行先进入模型上下文）。
    """
    excerpts: dict[str, str] = {}
    for event in events:
        if event["type"] != "tool.result":
            continue
        payload = require_object(event["payload"])
        if require_text(payload["name"]) in DOC_TOOL_NAMES:
            continue
        text = json.dumps(payload["result"], ensure_ascii=False)
        for name in record_ids(text):
            excerpts.setdefault(name, text[:RECORD_EXCERPT_CAP])
    return excerpts


def seen_ids(events: Sequence[Event]) -> set[str]:
    """恢复轮内已见编号集合（编造核对重放的对照）：工具返回 + 用户消息 + 简报。

    在线口径 shared.seen 的回放投影：fold_finding_record_ids 每轮并入 findings
    的编号，用户消息与 case 值在轮初并入；事件库按全会话取并集是保守上界。
    """
    seen: set[str] = set()
    for event in events:
        payload = require_object(event["payload"])
        if event["type"] == "tool.result":
            seen.update(record_ids(json.dumps(payload, ensure_ascii=False)))
        elif event["type"] == "message.user":
            seen.update(record_ids(require_text(payload["content"])))
        elif event["type"] == "run.finished":
            state = require_object(payload.get("state") or {})
            findings = require_list(state.get("findings") or [])
            for item in findings:
                finding = require_object(item)
                seen.update(record_ids(require_text(finding.get("digest", ""))))
                seen.update(record_ids(require_text(finding.get("target", ""))))
            case = require_object(state.get("case") or {})
            for value in require_object(case.get("known") or {}).values():
                seen.update(record_ids(require_text(value)))
    return seen


@dataclass(frozen=True)
class AuditInput:
    """校验器的输入包（specs/010 FR-1 证据装配的回放侧来源）。

    text 是重放确定性校验链后的文本（与在线 audited_response 输出同构）；
    passages 是本轮证据池（PassageSet 恢复）；records 是记录编号 → 摘录；
    budget 是片段字符封顶（回放走 CLI，不进配置面，research D9）。
    question_id 供 fixture 桩按题注入判定；真实校验器可忽略。
    """

    text: str
    question: str
    passages: tuple[Passage, ...]
    records: Mapping[str, str]
    budget: int = 8000
    question_id: str = ""


class ReplayVerifier(Protocol):
    """回放校验器的最小调用面：给定 gate 输入产出逐断言判定。

    真实 core 校验器（树A，T018 合并后）经 --verifier 注入同签名接入；
    fixture 桩只验证回放机制（SC-4 的机制预演），判定不是语义结论。
    """

    async def audit(self, request: AuditInput) -> tuple[AssertionVerdict, ...]: ...


@dataclass(frozen=True)
class ReplayCase:
    """一道题的回放输入与判定上下文。

    draft 为空时 text 也是空串：该题无发布草稿，branch 记录原因——
    light_branch（轻分支不在校验范围）或 clarify_terminal（追问终态无作答），
    这两者是正常语义不是缺陷，不进 deviations（占位轮仍算缺失，记
    no_answer_draft）。
    """

    question_id: str
    question_type: str
    multi_turn: bool
    task_pass: bool
    evidence_pass: bool
    session_id: str | None
    question: str
    branch: str
    draft: str | None
    text: str
    passages: tuple[Passage, ...]
    records: Mapping[str, str]
    seen: frozenset[str]
    deviations: list[str] = field(default_factory=list)


def replay_chain(draft: str) -> str:
    """重放确定性校验链：与在线 audited_response 相同的引用标记校验。

    编造核对不在此处：DISPUTE 重试链的终态已是恢复的草稿本身，回放以
    seen_ids 对照校验（check_invented），不重试。
    """
    return check_citations(draft).text


def check_invented(case: ReplayCase) -> list[str]:
    """编造核对重放：终态草稿的记录编号必须都在轮内已见集合里。

    在线终态不该有编造编号（DISPUTE 重试后仍编造才接受，属罕见边缘）；
    回放发现即记 deviation——终态假设失效要暴露，不能静默当成正常判定。
    """
    draft = case.draft or ""
    return sorted({name for name in record_ids(draft) if name not in case.seen})


def selected_cases(per_type: int) -> list[JsonObject]:
    """按 run.sample 口径筛题：每题型前 per_type 道；0 表示全量（--all 口径）。"""
    return sample(blueprints(), per_type)


def restore_case(
    row: Row,
    case: JsonObject,
    session_id: str | None,
    events: Sequence[Event] | None,
) -> ReplayCase:
    """把一题的事件恢复成回放输入：草稿 + 校验链重放 + 证据 + 摘录 + 已见编号。

    会话缺失记 deviation；轻分支与 clarify 终态是正常语义（branch 字段记录），
    只有 synthesize 路径下草稿缺失（占位轮）才算偏差。证据与摘录仍恢复——
    断点归类需要"期望证据是否被取到"的状态，与有无草稿无关。
    """
    question = question_of(case)
    deviations: list[str] = []
    draft: str | None = None
    branch = "session_missing"
    passages: tuple[Passage, ...] = ()
    records: Mapping[str, str] = {}
    seen: frozenset[str] = frozenset()
    if events is None:
        deviations.append("session_missing")
    else:
        passages = tuple(passages_of(events).all())
        records = record_excerpts_of(events)
        seen = frozenset(seen_ids(events))
        draft, branch = draft_of(events)
        if draft is None and branch == "light_branch":
            # 轻分支下区分占位与正常：会话有 synthesize 请求说明不是纯轻分支，
            # 草稿缺失是占位轮（两次输出失败）；纯轻分支才是范围外。
            has_synthesize_request = any(
                event["type"] == "model.request"
                and "你是排查协调者"
                in str(require_object(event["payload"]).get("request") or "")
                for event in events
            )
            if has_synthesize_request:
                branch = "placeholder"
                deviations.append("no_answer_draft")
    return ReplayCase(
        question_id=row.question_id,
        question_type=row.question_type,
        multi_turn=row.multi_turn,
        task_pass=row.task_pass,
        evidence_pass=row.evidence_pass,
        session_id=session_id,
        question=question,
        branch=branch,
        draft=draft,
        text=replay_chain(draft) if draft is not None else "",
        passages=passages,
        records=records,
        seen=seen,
        deviations=deviations,
    )


def replay_arm(arm: Path, per_type: int) -> list[ReplayCase]:
    """回放一个臂：逐题恢复回放输入；输出顺序与题库筛选顺序一致。"""
    cases = selected_cases(per_type)
    sessions = read_session_events(arm)
    matched = match_sessions(cases, sessions)
    rows = {row.question_id: row for row in load_rows(arm)}
    result: list[ReplayCase] = []
    for case in cases:
        question_id = require_text(case["question_id"])
        row = rows.get(question_id)
        if row is None:
            raise ValueError(f"{arm} 结果文件缺题 {question_id}")
        session_id = matched.get(question_id)
        events = sessions.get(session_id) if session_id is not None else None
        result.append(restore_case(row, case, session_id, events))
    return result


# ---- 校验器桩：机制验证用的本地判定，零模型调用 ----


def normalize_refs(sentence: str) -> tuple[str, ...]:
    """句中片段引用的规范化：[doc:X#cN] 与裸 X#cN 统一为 doc:X#cN 形态。

    证据池片段 id 都是 doc: 前缀形态（Passage.id），规范化后直接做存在性比对；
    同一引用重复出现只留一个，保序。
    """
    refs: list[str] = []
    for match in PASSAGE_REF.finditer(sentence):
        ref = match.group(0).strip("[]")
        refs.append(ref if ref.startswith("doc:") else f"doc:{ref}")
    return tuple(dict.fromkeys(refs))


@dataclass(frozen=True)
class FixtureOverride:
    """fixture 判定覆写：按断言文本的子串命中替换桩的词面判定。"""

    match: str
    verdict: Verdict
    overreach: Overreach | None = None
    revision: str | None = None
    conflict: Conflict | None = None


def load_fixture(path: Path | None) -> dict[str, list[FixtureOverride]]:
    """读判定 fixture：question_id → 逐条覆写；None 表示不注入。

    条目字段对齐 verdict-schema 的 verdict 条目（match/verdict/overreach/
    revision/conflict），解析失败直接报错——fixture 是受控输入，静默丢弃
    会把"该出现的判定没出现"藏进冒烟结果。
    """
    if path is None:
        return {}
    raw = require_object(parse_json(path.read_text(encoding="utf-8")))
    overrides: dict[str, list[FixtureOverride]] = {}
    for question_id, items in raw.items():
        entries: list[FixtureOverride] = []
        for item in require_list(items):
            entry = require_object(item)
            verdict = require_text(entry["verdict"])
            if verdict not in VERDICT_LABELS:
                raise ValueError(f"fixture 判定档非法: {verdict}")
            raw_overreach = entry.get("overreach")
            raw_conflict = entry.get("conflict")
            overreach = (
                Overreach(
                    dimension=cast(
                        "OverreachDimension",
                        require_text(require_object(raw_overreach)["dimension"]),
                    ),
                    note=require_text(require_object(raw_overreach)["note"]),
                )
                if raw_overreach is not None
                else None
            )
            conflict = (
                Conflict(
                    sources=tuple(
                        require_text(s)
                        for s in require_list(require_object(raw_conflict)["sources"])
                    ),
                    point=require_text(require_object(raw_conflict)["point"]),
                )
                if raw_conflict is not None
                else None
            )
            raw_revision = entry.get("revision")
            entries.append(
                FixtureOverride(
                    match=normalize(require_text(entry["match"])),
                    verdict=cast("Verdict", verdict),
                    overreach=overreach,
                    revision=None
                    if raw_revision is None
                    else require_text(raw_revision),
                    conflict=conflict,
                )
            )
        overrides[question_id] = entries
    return overrides


class FixtureVerifier:
    """机制验证桩（树A 合并前的默认校验器）：ID 存在性 + 词面支持两级判定。

    筛选与在线 gate 同口径（带引用标记的断言句、已带"未能核实"豁免）；词面
    口径与评测判分同构（去空白子串命中 → supported）。partial/contradicted
    由 --fixture 注入——桩不冒充语义判定，只验证回放链路与归类机制（SC-4
    的机制预演）；真实 core 校验器在 T018 后经 --verifier 注入替换。
    """

    def __init__(
        self, fixture: Mapping[str, list[FixtureOverride]] | None = None
    ) -> None:
        """绑定可选的逐题判定覆写。"""
        self.fixture: Mapping[str, list[FixtureOverride]] = fixture or {}

    async def audit(self, request: AuditInput) -> tuple[AssertionVerdict, ...]:
        """对 gate 输入逐句判定；无断言句返回空（零断言放行路径）。

        句子剥掉切分残留的前导空白后再进判定与 JSONL——与在线 gate 的锚点
        口径一致（assertion.text 是句子正文）。
        """
        passage_ids = {passage.id for passage in request.passages}
        verdicts: list[AssertionVerdict] = []
        for raw in split_sentences(request.text):
            sentence = raw.strip()
            if not has_citation(sentence) or not is_assertion(sentence):
                continue
            assertion = Assertion(
                text=sentence,
                refs=normalize_refs(sentence),
                record_ids=tuple(dict.fromkeys(record_ids(sentence))),
            )
            verdicts.append(
                self._judge(
                    assertion,
                    passage_ids,
                    request.passages,
                    request.records,
                    request.question_id,
                    self.fixture,
                )
            )
        return tuple(verdicts)

    def _judge(
        self,
        assertion: Assertion,
        passage_ids: set[str],
        passages: Sequence[Passage],
        records: Mapping[str, str],
        question_id: str,
        overrides: Mapping[str, list[FixtureOverride]],
    ) -> AssertionVerdict:
        """单条断言判定：fixture 覆写优先，否则 ID 存在性 → 词面支持。"""
        for entry in overrides.get(question_id, ()):
            if normalize(entry.match) in normalize(assertion.text):
                return AssertionVerdict(
                    assertion=assertion,
                    verdict=entry.verdict,
                    overreach=entry.overreach,
                    revision=entry.revision,
                    conflict=entry.conflict,
                )
        return self._rule_verdict(assertion, passage_ids, passages, records)

    def _rule_verdict(
        self,
        assertion: Assertion,
        passage_ids: set[str],
        passages: Sequence[Passage],
        records: Mapping[str, str],
    ) -> AssertionVerdict:
        """确定性规则判定（无 fixture 时）：ID 存在性优先，词面支持兜底。"""
        missing_refs = [ref for ref in assertion.refs if ref not in passage_ids]
        missing_records = [name for name in assertion.record_ids if name not in records]
        if missing_refs or missing_records:
            return AssertionVerdict(assertion=assertion, verdict="insufficient")
        bound = [passage for passage in passages if passage.id in assertion.refs]
        candidates = bound or list(passages)
        if any(text_supported(item.text, assertion.text) for item in candidates):
            return AssertionVerdict(assertion=assertion, verdict="supported")
        return AssertionVerdict(assertion=assertion, verdict="insufficient")


def text_supported(passage_text: str, assertion: str) -> bool:
    """片段文本是否覆盖断言：与评测判分同口径（子串命中，或二元组重叠过半）。

    run.supported 的"作答覆盖断言"形态反过来用在"片段覆盖断言"上——桩的词面
    支持与评测判分共用同一算法，判定不是语义结论（真实语义校验由树A 校验器
    承载），只验证回放链路与归类机制。比对前剥掉句内引用标记：标记是依据
    指针不是断言内容，计入词面会把"，见 [doc:...]"的字符当事实比对。
    """
    source = "".join(passage_text.split())
    target = PASSAGE_REF.sub("", assertion)
    target = "".join(target.split())
    if not target:
        return False
    if target in source:
        return True
    grams = {target[index : index + 2] for index in range(len(target) - 1)}
    return bool(grams) and sum(gram in source for gram in grams) / len(grams) >= 0.5


def verdict_payload(verdict: AssertionVerdict) -> JsonObject:
    """判定条目的 JSON 形态：与 verdict-schema.json 的 verdicts 条目同构。"""
    payload: JsonObject = {
        "text": verdict.assertion.text,
        "refs": list(verdict.assertion.refs),
        "record_ids": list(verdict.assertion.record_ids),
        "verdict": verdict.verdict,
    }
    if verdict.overreach is not None:
        payload["overreach"] = {
            "dimension": verdict.overreach.dimension,
            "note": verdict.overreach.note,
        }
    if verdict.revision is not None:
        payload["revision"] = verdict.revision
    if verdict.conflict is not None:
        payload["conflict"] = {
            "sources": list(verdict.conflict.sources),
            "point": verdict.conflict.point,
        }
    return payload


@dataclass(frozen=True)
class AuditRow:
    """一题的判定行：逐断言判定 + 分支与偏差，JSONL 的最小单元。

    required 是题库必需断言文本（误伤基线对照面）；breakpoints 是断点归类
    （T010）。
    """

    question_id: str
    question_type: str
    multi_turn: bool
    task_pass: bool
    evidence_pass: bool
    session_id: str | None
    branch: str
    verdicts: tuple[AssertionVerdict, ...]
    deviations: tuple[str, ...]
    required: tuple[str, ...] = ()
    breakpoints: tuple[BreakpointEntry, ...] = ()


async def audit_case(
    case: ReplayCase,
    verifier: ReplayVerifier,
    required: Sequence[str] = (),
    expected: ExpectedEvidence | None = None,
) -> AuditRow:
    """执行一题的校验器回放；无草稿题零判定并保留分支与偏差。

    归类上下文在判定完成后构造：本轮证据状态（PassageSet 标题 + 已见记录）
    与题库期望证据（expected）合成四类断点的判定输入。
    """
    verdicts: tuple[AssertionVerdict, ...] = ()
    if case.draft is not None:
        verdicts = await verifier.audit(
            AuditInput(
                text=case.text,
                question=case.question,
                passages=case.passages,
                records=case.records,
                question_id=case.question_id,
            )
        )
    passage_headings, ref_headings = passage_headings_of(case.passages)
    context = BreakpointContext(
        passage_headings=passage_headings,
        seen_records=frozenset(case.seen),
        ref_headings=ref_headings,
        expected=expected,
    )
    row = AuditRow(
        question_id=case.question_id,
        question_type=case.question_type,
        multi_turn=case.multi_turn,
        task_pass=case.task_pass,
        evidence_pass=case.evidence_pass,
        session_id=case.session_id,
        branch=case.branch,
        verdicts=verdicts,
        deviations=(
            *case.deviations,
            *(f"invented:{name}" for name in check_invented(case)),
        ),
        required=tuple(required),
    )
    return replace(row, breakpoints=tuple(breakpoint_entries(row, context)))


def required_assertions(case: JsonObject) -> tuple[str, ...]:
    """题的必需断言文本（ground_truth.required_assertions），误伤基线对照面。"""
    truth = require_object(case["ground_truth"])
    return tuple(
        require_text(require_object(item)["text"])
        for item in require_list(truth.get("required_assertions") or [])
    )


@dataclass(frozen=True)
class CollateralEntry:
    """一条误伤样例：通过题的必需断言被判为 partial/contradicted。"""

    question_id: str
    assertion: str
    verdict: Verdict
    matched: tuple[str, ...]


def collateral_entries(row: AuditRow) -> list[CollateralEntry]:
    """一题的误伤条目：只看通过题（task_pass=true），只看 partial/contradicted。

    "必需断言被误判"按评测同口径对齐：判定句文本对该必需断言命中（子串/二元
    组重叠过半），即这句作答承载了该必需断言——校验器收紧/降级它会直接影响
    task_pass（SC-6 通过题转失败口径的预测源）。
    """
    if not row.task_pass:
        return []
    entries: list[CollateralEntry] = []
    for verdict in row.verdicts:
        if verdict.verdict not in ("partial", "contradicted"):
            continue
        matched = tuple(
            text
            for text in row.required
            if text_supported(verdict.assertion.text, text)
        )
        if matched:
            entries.append(
                CollateralEntry(
                    question_id=row.question_id,
                    assertion=verdict.assertion.text,
                    verdict=verdict.verdict,
                    matched=matched,
                )
            )
    return entries


@dataclass(frozen=True)
class CollateralBaseline:
    """误伤基线（SC-6 go/no-go 数据源）：比例 + 逐条样例。"""

    passed_required_total: int
    harmed: int
    samples: tuple[CollateralEntry, ...]

    @property
    def rate(self) -> float:
        """误伤比例：误伤断言条数 / 通过题必需断言总数；无通过题时为 0。"""
        return (
            self.harmed / self.passed_required_total
            if self.passed_required_total
            else 0.0
        )


def collateral_baseline(rows: Sequence[AuditRow]) -> CollateralBaseline:
    """汇总误伤基线：通过题必需断言总数为分母，覆盖误判条数为分子。"""
    passed_required_total = sum(len(row.required) for row in rows if row.task_pass)
    samples: list[CollateralEntry] = []
    for row in rows:
        samples.extend(collateral_entries(row))
    return CollateralBaseline(
        passed_required_total=passed_required_total,
        harmed=len(samples),
        samples=tuple(samples),
    )


# ---- 断点归类（specs/010 FR-9/T010）：四类断点的判定×期望证据命中规则 ----

# 四类断点词汇（data-model AssertionBreakpoint）；键 = 归类依据的简述。
BREAKPOINT_LABELS: dict[str, str] = {
    "overreach": "表述越界",
    "not_recalled": "没召回",
    "mis_bound": "绑错",
    "contradicted": "矛盾",
}


@dataclass(frozen=True)
class ExpectedEvidence:
    """题库期望证据的翻译结果（evidence.py 对齐器口径）。

    headings 是期望文档块的运行侧小节标题集（chunk_alignment 反查）；records
    是期望业务记录编号。expected=None 表示无题库对照（数据集缺失），归类退化
    为不猜——不把"没对照"算成"没召回"。
    """

    headings: frozenset[str] = frozenset()
    records: frozenset[str] = frozenset()


@dataclass(frozen=True)
class BreakpointEntry:
    """一条断点归类：判定档 × 期望证据命中状态 → 四类之一（或不归类）。"""

    question_id: str
    question_type: str
    task_pass: bool
    breakpoint: str
    rule: str
    assertion: str
    recalled: bool | None


@dataclass(frozen=True)
class BreakpointContext:
    """一题的归类上下文：本轮证据状态与题库期望证据。

    passage_headings/seen_records 是本轮"已取到"的证据状态（PassageSet 恢复 +
    tool.result 记录恢复）；ref_headings 把断言引用翻成小节标题（绑定判定用）。
    expected 为 None 表示无题库对照。
    """

    passage_headings: frozenset[str] = frozenset()
    seen_records: frozenset[str] = frozenset()
    ref_headings: Mapping[str, str] = field(default_factory=lambda: dict[str, str]())
    expected: ExpectedEvidence | None = None


def classify_breakpoint(
    verdict: AssertionVerdict, context: BreakpointContext
) -> tuple[str, str, bool | None]:
    """单条判定的断点归类；返回 (断点, 依据, 期望证据命中状态)。

    规则（spec US2）：partial→overreach；contradicted→contradicted；
    insufficient ∧ 期望证据∉本轮证据集→not_recalled；∈集 ∧ 未绑定→mis_bound。
    supported 与无对照不进四类（断点只解释失败与矛盾形态）。
    """
    if verdict.verdict == "partial":
        return "overreach", "partial", None
    if verdict.verdict == "contradicted":
        return "contradicted", "contradicted", None
    if verdict.verdict != "insufficient":
        return "", "supported", None
    if context.expected is None:
        return "", "no_expected_evidence", None
    expected = context.expected
    doc_recalled = bool(expected.headings & context.passage_headings)
    record_recalled = bool(expected.records & context.seen_records)
    recalled = doc_recalled or record_recalled
    bound_headings = {
        context.ref_headings.get(ref, "")
        for ref in verdict.assertion.refs
        if context.ref_headings.get(ref, "")
    }
    bound_records = set(verdict.assertion.record_ids)
    bound = bool(bound_headings & expected.headings) or bool(
        bound_records & expected.records
    )
    if not recalled:
        return "not_recalled", "insufficient∧期望证据∉本轮证据集", False
    if not bound:
        return "mis_bound", "insufficient∧期望证据∈集∧未绑定", True
    return "", "insufficient∧期望证据∈集∧已绑定（四类外）", True


def breakpoint_entries(
    row: AuditRow, context: BreakpointContext
) -> list[BreakpointEntry]:
    """一题的逐断言归类：supported/无对照不产出条目。"""
    entries: list[BreakpointEntry] = []
    for verdict in row.verdicts:
        breakpoint, rule, recalled = classify_breakpoint(verdict, context)
        if not breakpoint:
            continue
        entries.append(
            BreakpointEntry(
                question_id=row.question_id,
                question_type=row.question_type,
                task_pass=row.task_pass,
                breakpoint=breakpoint,
                rule=rule,
                assertion=verdict.assertion.text,
                recalled=recalled,
            )
        )
    return entries


@dataclass(frozen=True)
class BreakpointSummary:
    """四类断点分布 + 逐条样例（FR-9 汇总报告的数据形态）。"""

    counts: Mapping[str, int]
    samples: tuple[BreakpointEntry, ...]

    @property
    def contradiction_share(self) -> float:
        """矛盾档占比：矛盾样例数 / 全部归类条数；无条目时为 0。"""
        total = sum(self.counts.values())
        return self.counts.get("contradicted", 0) / total if total else 0.0


def breakpoint_summary(rows: Sequence[AuditRow]) -> BreakpointSummary:
    """汇总断点分布与样例：归类已在 audit_case 内完成，这里聚合。"""
    counts: dict[str, int] = {name: 0 for name in BREAKPOINT_LABELS}
    samples: list[BreakpointEntry] = []
    for row in rows:
        for entry in row.breakpoints:
            counts[entry.breakpoint] = counts.get(entry.breakpoint, 0) + 1
            samples.append(entry)
    return BreakpointSummary(counts=counts, samples=tuple(samples))


def passage_headings_of(
    passages: Sequence[Passage],
) -> tuple[frozenset[str], Mapping[str, str]]:
    """本轮证据池的标题状态：标题集与 id→标题索引（evidence.section_of 口径）。"""
    headings: set[str] = set()
    ref_headings: dict[str, str] = {}
    for item in passages:
        heading = evidence.section_of(item.id, item.source.locator)
        headings.add(heading)
        ref_headings[item.id] = heading
    return frozenset(headings), ref_headings


def expected_evidence_of(
    question_id: str, alignment: Mapping[str, str]
) -> ExpectedEvidence | None:
    """题库期望证据 → 运行侧词汇（evidence.expected_ids + chunk_alignment）。

    数据集不可得（FLOWPAY_DATASET_ROOT 未设）时返回 None：归类退化不猜，
    没有标准答案就无法区分"没召回"与"绑错"（research D7）。
    """
    try:
        documents, records = evidence.expected_ids(question_id)
    except SystemExit:
        return None
    headings = frozenset(
        alignment[identifier] for identifier in documents if alignment.get(identifier)
    )
    record_names = frozenset(name.removeprefix("record:") for name in records)
    return ExpectedEvidence(headings=headings, records=record_names)


def expected_evidence_map(
    question_ids: Iterable[str], alignment: Mapping[str, str] | None = None
) -> dict[str, ExpectedEvidence | None]:
    """全部候选题的期望证据映射；对齐器缺数据集时对齐表取空。"""
    if alignment is None:
        try:
            alignment = evidence.chunk_alignment()
        except SystemExit:
            alignment = {}
    return {
        question_id: expected_evidence_of(question_id, alignment)
        for question_id in question_ids
    }


def breakpoint_payload(entry: BreakpointEntry) -> JsonObject:
    """断点条目的 JSON 形态：归类 + 依据 + 期望证据命中状态。"""
    return {
        "breakpoint": entry.breakpoint,
        "rule": entry.rule,
        "assertion": entry.assertion,
        "recalled": entry.recalled,
    }


def write_verdicts(path: Path, rows: Sequence[AuditRow]) -> None:
    """逐题逐断言判定 JSONL：一行一题，verdicts 条目与契约同构。"""
    lines: list[str] = []
    for row in rows:
        lines.append(
            json.dumps(
                {
                    "question_id": row.question_id,
                    "question_type": row.question_type,
                    "multi_turn": row.multi_turn,
                    "task_pass": row.task_pass,
                    "evidence_pass": row.evidence_pass,
                    "session_id": row.session_id,
                    "branch": row.branch,
                    "deviations": list(row.deviations),
                    "required": list(row.required),
                    "verdicts": [verdict_payload(verdict) for verdict in row.verdicts],
                    "breakpoints": [
                        breakpoint_payload(entry) for entry in row.breakpoints
                    ],
                },
                ensure_ascii=False,
            )
        )
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def render_verdict_report(rows: Sequence[AuditRow]) -> str:
    """判定汇总（T009）：四档分布与逐档样例；断点归类报告由 T010/T011 扩展。"""
    counts = Counter(verdict.verdict for row in rows for verdict in row.verdicts)
    branches = Counter(row.branch for row in rows)
    branch_summary = (
        f"- 回放题数：{len(rows)}（synthesize 作答 {branches.get('synthesize_answer', 0)}；"
        f"轻分支 {branches.get('light_branch', 0)}；clarify 终态 {branches.get('clarify_terminal', 0)}；"
        f"占位 {branches.get('placeholder', 0)}）"
    )
    lines = [
        "# 010 逐断言校验回放报告",
        "",
        branch_summary,
        f"- 断言总数：{sum(len(row.verdicts) for row in rows)}；"
        + "、".join(
            f"{VERDICT_LABELS[kind]} {counts.get(kind, 0)}" for kind in VERDICT_LABELS
        ),
        "",
    ]
    for kind in ("partial", "contradicted", "insufficient"):
        samples = [
            (row.question_id, verdict)
            for row in rows
            for verdict in row.verdicts
            if verdict.verdict == kind
        ]
        lines.append(f"## {VERDICT_LABELS[kind]}样例（{len(samples)} 条）")
        lines.append("")
        for question_id, verdict in samples[:5]:
            note = verdict.overreach.note if verdict.overreach else ""
            lines.append(f"- `{question_id}` {verdict.assertion.text} {note}".rstrip())
        lines.append("")
    lines.extend(breakpoint_section(breakpoint_summary(rows)))
    lines.extend(collateral_section(collateral_baseline(rows)))
    return "\n".join(lines)


def breakpoint_section(summary: BreakpointSummary) -> list[str]:
    """断点归类段（T010）：四类分布 + 矛盾占比 + 逐条样例（FR-9）。"""
    total = sum(summary.counts.values())
    lines = [
        "## 断点归类分布（四类）",
        "",
        "、".join(
            f"{BREAKPOINT_LABELS[name]} {summary.counts.get(name, 0)}"
            for name in BREAKPOINT_LABELS
        )
        + f"（归类条数合计 {total}；矛盾档占比 {summary.contradiction_share:.1%}）",
        "",
    ]
    for name, label in BREAKPOINT_LABELS.items():
        matched = [entry for entry in summary.samples if entry.breakpoint == name]
        if not matched:
            continue
        lines.append(f"### {label}样例（{len(matched)} 条）")
        lines.append("")
        for entry in matched[:5]:
            status = "已召回" if entry.recalled else "未召回"
            lines.append(f"- `{entry.question_id}` {status}｜{entry.assertion}")
        lines.append("")
    return lines


def collateral_section(baseline: CollateralBaseline) -> list[str]:
    """误伤基线段（T011）：比例 + 逐条样例，SC-6 go/no-go 的数据源。"""
    summary = (
        f"- 通过题必需断言总数：{baseline.passed_required_total}；"
        f"误伤 {baseline.harmed} 条，比例 {baseline.rate:.1%}"
    )
    lines = [
        "## 误伤基线（通过题必需断言被判 partial/contradicted）",
        "",
        summary,
        "",
    ]
    for entry in baseline.samples:
        matched = "、".join(f"「{text}」" for text in entry.matched)
        lines.append(
            f"- `{entry.question_id}` [{entry.verdict}] {entry.assertion} ← {matched}"
        )
    lines.append("")
    return lines


def load_verifier(
    spec: str | None, fixture: dict[str, list[FixtureOverride]]
) -> ReplayVerifier:
    """装配校验器：--verifier module:attr（无参构造）优先，默认 fixture 桩。

    真实 core 校验器（树A）T018 合并后按此注入；注入物必须实现
    ReplayVerifier.audit 的调用面，回放脚本不做二次适配。
    """
    if spec is None:
        return FixtureVerifier(fixture)
    module_path, _, name = spec.partition(":")
    factory = cast(
        "Callable[[], ReplayVerifier]",
        getattr(importlib.import_module(module_path), name),
    )
    return factory()


async def audit_arm(
    cases: Sequence[ReplayCase],
    verifier: ReplayVerifier,
    required: Mapping[str, Sequence[str]] | None = None,
    expected: Mapping[str, ExpectedEvidence | None] | None = None,
) -> list[AuditRow]:
    """逐题执行校验器；顺序执行足够（判定是本地计算，无并发需求）。"""
    required = required or {}
    expected = expected or {}
    return [
        await audit_case(
            case,
            verifier,
            required.get(case.question_id, ()),
            expected.get(case.question_id),
        )
        for case in cases
    ]


class Arguments(argparse.Namespace):
    """命令行参数；run.Arguments 先例的回放版。

    required 字段的默认值只是类型层占位（argparse required=True 运行时强制
    传入），与 run.Arguments 的非空默认同一形态。
    """

    arm: Path = Path(".")
    sample: int = 1
    all: bool = False
    report: Path = Path(".")
    fixture: Path | None = None
    verifier: str | None = None


def main(argv: Sequence[str] | None = None) -> None:
    """CLI 入口：--arm/--sample/--all/--report，判定 JSONL 与报告写入 --report 目录。"""
    usage = __doc__ or "010 逐断言校验的旁路回放驱动"
    parser = argparse.ArgumentParser(description=usage.splitlines()[0])
    parser.add_argument(
        "--arm",
        type=Path,
        required=True,
        help="验收臂目录（含 multi-agent.jsonl 与 multi-agent/sessions.sqlite）",
    )
    parser.add_argument(
        "--sample", type=int, default=1, help="每题型抽题数（run.sample 口径）；默认 1"
    )
    parser.add_argument(
        "--all", action="store_true", help="全量回放（96 题量级，批准门后执行）"
    )
    parser.add_argument(
        "--report", type=Path, required=True, help="输出目录；写入判定 JSONL 与汇总报告"
    )
    parser.add_argument(
        "--fixture", type=Path, default=None, help="判定 fixture JSON（覆写桩判定）"
    )
    parser.add_argument(
        "--verifier", default=None, help="真实校验器注入（module:attr，无参构造）"
    )
    args = cast("Arguments", parser.parse_args(argv))
    if args.all:
        if args.sample != 1:
            parser.error("--all 与 --sample 互斥")
        per_type = 0
    else:
        per_type = args.sample
    cases = replay_arm(args.arm, per_type)
    required = {
        require_text(case["question_id"]): required_assertions(case)
        for case in selected_cases(per_type)
    }
    expected = expected_evidence_map(
        require_text(c["question_id"]) for c in selected_cases(per_type)
    )
    verifier = load_verifier(args.verifier, load_fixture(args.fixture))
    rows = asyncio.run(audit_arm(cases, verifier, required, expected))
    out_dir = args.report
    out_dir.mkdir(parents=True, exist_ok=True)
    write_verdicts(out_dir / "assertion-verdicts.jsonl", rows)
    (out_dir / "assertion-report.md").write_text(
        render_verdict_report(rows), encoding="utf-8"
    )
    print(f"replayed {len(rows)} questions -> {out_dir}")


if __name__ == "__main__":
    main()
