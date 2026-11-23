"""统一作答出口后的逐断言校验门（specs/010 US3/T014）。

接线点唯一：synthesize_node 在 audited_response 产物发布前单点调用
gate_response（T015，树B 唯一既有文件改动）。开关关闭（默认）时零模型调用、
零事件，行为与 009 一致（SC-1）；开启时筛选带引用标记的断言句送 core 校验器，
处置结果按句锚点回填发布文本，判定/处置/fail-open 落 assertion.audit 事件
（JSON 事件通道，前端与报表对未知事件不阻断）。

校验器调用不计入 worker 工具预算（specs/009 FR-9 口径不变）；轮次预算耗尽
跳过校验并发事件（等效 fail-open，specs/010 边界情况）。校验器任一环节故障
fail-open 放行原文：不阻断回答、不产生 run.failed、不改变轮次终态（FR-6）。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from flowpay.core.event_log.contracts import EventSink
from flowpay.core.json import JsonObject
from flowpay.core.knowledge.contracts import Passage
from flowpay.core.verify import (
    AssertionAction,
    AssertionAudit,
    AssertionVerdict,
)

from .citation_check import (
    BULLET_PREFIX,
    UNVERIFIED_MARK,
    has_citation,
    is_assertion,
    split_sentences,
)

# 事件类型名（specs/010 data-model）：判定清单、处置动作、fail_open 与矛盾
# 分歧详情（verdicts 条目内 conflict 字段）一次携带。
AUDIT_EVENT = "assertion.audit"

# 句级筛选与处置回填的默认证据预算回退值：Settings 缺字段时同树A 默认。
DEFAULT_EVIDENCE_BUDGET = 8000


class AssertionAuditor(Protocol):
    """在线校验器的调用面：给定 gate 输入产出 AssertionAudit（specs/010 契约）。

    树A 校验器（T018 合并后）由装配方按此签名接入；记录摘录（FR-1 的事件侧
    数据源）属校验器装配层的职责——gate 只传已校验文本、本轮问题与证据池，
    不扩调用面字段。
    """

    async def audit(
        self, text: str, question: str, passages: Sequence[Passage]
    ) -> AssertionAudit: ...


@dataclass(frozen=True)
class AssertionGate:
    """一次在线校验的装配：开关、片段预算与校验器入口。

    enabled=False（默认）时 gate_response 直接返回原文；enabled=True 而
    auditor=None 是装配缺口，按 fail-open 记事件放行原文——校验器故障永不
    阻断回答（FR-6），装配缺口也不伪装成"关闭"（静默关闭会掩盖配置错误）。
    """

    enabled: bool
    budget: int = DEFAULT_EVIDENCE_BUDGET
    auditor: AssertionAuditor | None = None


@dataclass(frozen=True)
class GateOutcome:
    """一次门控的产物：发布文本与事件载荷（payload=None 表示不发事件）。"""

    text: str
    payload: JsonObject | None = None


def gate_from_settings(
    settings: object, auditor: AssertionAuditor | None = None
) -> AssertionGate | None:
    """从 Settings 构造门；断言字段缺失（树A 合并前）时默认关闭。

    getattr 兜底是消费侧对桩的契约：字段名以树A T013 为准
    （assertion_audit_enabled / assertion_audit_evidence_budget），树B 不读
    配置文件、不扩配置面。
    """
    if not getattr(settings, "assertion_audit_enabled", False):
        return None
    budget = int(getattr(settings, "assertion_audit_evidence_budget", DEFAULT_EVIDENCE_BUDGET))
    return AssertionGate(enabled=True, budget=budget, auditor=auditor)


def select_assertions(text: str) -> list[str]:
    """句级筛选：带引用标记的断言句集合；已带"未能核实"口径句天然豁免。

    口径与回放侧一致（citation_check 的 is_assertion 豁免形态）；纯操作指引、
    客套话（无线索词）不进校验（spec 边界：零断言放行不降级）。
    """
    return [
        sentence
        for sentence in split_sentences(text)
        if has_citation(sentence) and is_assertion(sentence)
    ]


def downgrade_text(sentence: str) -> str:
    """降级文本：与 citation_check.downgrade 同一口径（bullet 前缀原样保留）。"""
    bullet = BULLET_PREFIX.match(sentence)
    prefix = bullet.group(0) if bullet else ""
    body = sentence[len(prefix) :] if prefix else sentence
    return f"{prefix}{UNVERIFIED_MARK}：{body}"


def replacement_of(action: AssertionAction) -> str | None:
    """动作的替换文本：keep 不替换；replace 用 revision（缺失降级兜底）；downgrade 统一口径。"""
    if action.action == "keep":
        return None
    if action.action == "replace":
        return action.revision or downgrade_text(action.sentence)
    return downgrade_text(action.sentence)


def apply_actions(text: str, actions: Sequence[AssertionAction]) -> str:
    """把处置动作按句锚点回填原文：句级等值匹配，命中多处取第一处。

    锚点失配（校验器输出与 gate 筛选不一致的边缘）时该句原样保留——替换错位
    是安全事故，放行错位句是可观测的小损失（research D5 安全侧）。
    """
    if not actions:
        return text
    replacements: dict[str, str] = {}
    for action in actions:
        replacement = replacement_of(action)
        if replacement is not None and action.sentence not in replacements:
            replacements[action.sentence] = replacement
    if not replacements:
        return text
    return "".join(replacements.get(sentence, sentence) for sentence in split_sentences(text))


def audit_payload(audit: AssertionAudit) -> JsonObject:
    """AssertionAudit → assertion.audit 载荷（verdict-schema 同构）。"""
    return {
        "verdicts": [verdict_payload(verdict) for verdict in audit.verdicts],
        "actions": [action_payload(action) for action in audit.actions],
        "fail_open": audit.fail_open,
        "text": audit.text,
    }


def verdict_payload(verdict: AssertionVerdict) -> JsonObject:
    """判定条目的 JSON 形态：含矛盾 conflict 详情（D10 落事件）。"""
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


def action_payload(action: AssertionAction) -> JsonObject:
    """处置条目的 JSON 形态：sentence 锚点 + 动作 + 替换文本。"""
    payload: JsonObject = {"sentence": action.sentence, "action": action.action}
    if action.revision is not None:
        payload["revision"] = action.revision
    return payload


async def gate_response(
    text: str,
    gate: AssertionGate | None,
    *,
    waiting: bool,
    question: str,
    passages: Sequence[Passage],
    tracer: EventSink,
    budget_exhausted: bool = False,
) -> str:
    """统一出口后的单点校验（T015 接线调用的入口）：发布文本的最终形态。

    waiting 轮（澄清追问正文是系统确定性拼接）与关闭态直接返回原文，零事件；
    其余路径都发一次 assertion.audit（零断言、fail-open、预算耗尽、正常判定），
    与 data-model 的"开关开启的每次最终作答校验后"一致。
    """
    if gate is None or not gate.enabled or waiting:
        return text
    if budget_exhausted or gate.auditor is None:
        audit = AssertionAudit(text=text, fail_open=True)
        await tracer.emit(AUDIT_EVENT, audit_payload(audit))
        return text
    assertions = select_assertions(text)
    try:
        audit = await gate.auditor.audit("\n".join(assertions), question, passages)
    except Exception:  # noqa: BLE001 - 校验器故障 fail-open，不上抛（FR-6）
        audit = AssertionAudit(text=text, fail_open=True)
    await tracer.emit(AUDIT_EVENT, audit_payload(audit))
    if audit.fail_open:
        return text
    return apply_actions(text, audit.actions)
