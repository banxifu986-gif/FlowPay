"""统一作答出口后的逐断言校验门（specs/010 US3/T014）。

接线点唯一：synthesize_node 在 audited_response 产物发布前单点调用
gate_response（T015，树B 唯一既有文件改动）。开关关闭（默认）时零模型调用、
零事件，行为与 009 一致（SC-1）；开启时筛选带引用标记的断言句送 core 校验器，
处置结果按句锚点回填发布文本，判定/处置/fail-open 落 assertion.audit 事件
（JSON 事件通道，前端与报表对未知事件不阻断）。

校验器调用不计入 worker 工具预算（specs/009 FR-9 口径不变），且与轮次预算
解耦：校验器调用走独立 ChatClient、不消耗轮次共享预算，因此轮次预算耗尽不
跳过校验——否则完整排查轮（预算先被 worker 调查耗尽）在线上永远走 fail-open、
从不被真正校验（2026-11-23 e2e 实测结论，specs/010 边界情况改写口径）。
校验器任一环节故障 fail-open 放行原文：不阻断回答、不产生 run.failed、不改变
轮次终态（FR-6）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
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
from flowpay.core.verify.actions import apply_actions

from .citation_check import has_citation, is_assertion, split_sentences

# 事件类型名（specs/010 data-model）：判定清单、处置动作、fail_open 与矛盾
# 分歧详情（verdicts 条目内 conflict 字段）一次携带。
AUDIT_EVENT = "assertion.audit"

# 句级筛选与处置回填的默认证据预算回退值：Settings 缺字段时同树A 默认。
DEFAULT_EVIDENCE_BUDGET = 8000


class AssertionAuditor(Protocol):
    """在线校验器的调用面：给定 gate 输入产出 AssertionAudit（specs/010 契约）。

    树A 校验器（T018 合并后）由装配方按此签名接入；tracer 是每轮事实
    （manager 每轮新建），下传让校验器的模型调用落当轮事件流。record_excerpts
    是 worker 业务查询登记进共享摘录池的记录摘录（FR-1 的"断言中记录编号对应
    记录摘录"在线口径），由 gate 调用点从 shared 取出后一并传入。
    """

    async def audit(
        self,
        text: str,
        question: str,
        passages: Sequence[Passage],
        *,
        tracer: EventSink,
        record_excerpts: Mapping[str, str],
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
    客套话（无线索词）不进校验（spec 边界：零断言放行不降级）。切句残留在句
    首的换行/空白一并剥掉：锚点语义是"句子正文"，处置回填按剥空后的句子
    等值匹配，前导空白回填时原样保留。
    """
    return [
        sentence.strip()
        for sentence in split_sentences(text)
        if has_citation(sentence) and is_assertion(sentence)
    ]


def audit_payload(audit: AssertionAudit, *, text: str | None = None) -> JsonObject:
    """AssertionAudit → assertion.audit 载荷（verdict-schema 同构）。

    text 覆写（审查 P2-1）：事件里的 text 语义是"发布的最终文本"——auditor
    返回的 audit.text 基于 gate 送入的候选句拼接，与发布文本不同形，一律以
    gate_response 的发布文本为准。
    """
    return {
        "verdicts": [verdict_payload(verdict) for verdict in audit.verdicts],
        "actions": [action_payload(action) for action in audit.actions],
        "fail_open": audit.fail_open,
        "text": text if text is not None else audit.text,
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
    record_excerpts: Mapping[str, str] | None = None,
) -> str:
    """统一出口后的单点校验（T015 接线调用的入口）：发布文本的最终形态。

    waiting 轮（澄清追问正文是系统确定性拼接）与关闭态直接返回原文，零事件；
    其余路径都发一次 assertion.audit（零断言、fail-open、正常判定），与
    data-model 的"开关开启的每次最终作答校验后"一致。record_excerpts 是
    shared 的记录摘录池（None 视为空池），随断言一并送校验器——FR-1 的记录
    摘录口径在线装配的入口。不看轮次预算：校验器调用走独立 ChatClient、
    不消耗轮次共享预算，预算耗尽跳过会让完整排查轮（worker 调查先耗尽预算）
    在线上永不校验——2026-11-23 e2e 实测结论。
    """
    if gate is None or not gate.enabled or waiting:
        return text
    excerpts: Mapping[str, str] = record_excerpts if record_excerpts is not None else {}
    if gate.auditor is None:
        # 装配缺口防线（enabled 而 auditor=None）：与预算无关，fail-open 记
        # 事件放行原文，装配缺口不伪装成"关闭"（静默关闭会掩盖配置错误）。
        audit = AssertionAudit(text=text, fail_open=True)
        await tracer.emit(AUDIT_EVENT, audit_payload(audit))
        return text
    assertions = select_assertions(text)
    if not assertions:
        # 零断言放行（spec 边界）：纯指引/客套/已带未证实口径的作答跳过校验，
        # 不发生校验器调用，事件按零判定记录（每次校验后落事件）。
        await tracer.emit(AUDIT_EVENT, audit_payload(AssertionAudit(text=text)))
        return text
    try:
        audit = await gate.auditor.audit(
            "\n".join(assertions),
            question,
            passages,
            tracer=tracer,
            record_excerpts=excerpts,
        )
    except Exception:  # noqa: BLE001 - 校验器故障 fail-open，不上抛（FR-6）
        audit = AssertionAudit(text=text, fail_open=True)
    published = text if audit.fail_open else apply_actions(text, audit.actions)
    await tracer.emit(AUDIT_EVENT, audit_payload(audit, text=published))
    return published
