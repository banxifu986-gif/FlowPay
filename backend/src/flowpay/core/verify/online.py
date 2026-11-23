"""在线校验器装配（specs/010 审查 P1-2）：AssertionAuditor 的主模型实现。

gate 由 bootstrap 一次性装配（跨轮共享），tracer 是每轮事实（manager 每轮
新建 Tracer）——audit 每次用当轮 tracer 构造 ChatClient（轻对象），校验器的
model.request/response 照常落当轮事件流。record_excerpts 是 worker 业务查询
登记进共享摘录池的记录摘录（FR-1 的"断言中记录编号对应记录摘录"在线口径），
由 gate 调用点从 shared 取出后传入，绑定记录编号的断言不再因摘录缺失必然降级
（2026-11-23 e2e 实测清偿；回放侧保持全口径）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from flowpay.core.event_log.contracts import EventSink
from flowpay.core.knowledge.contracts import Passage
from flowpay.core.llm.chat.client import ChatClient
from flowpay.core.llm.chat.contracts import ChatModel
from flowpay.core.verify.contracts import AssertionAudit
from flowpay.core.verify.errors import audit_response


class ModelAuditor:
    """复用主模型配置的在线校验器；协议形由消费侧（assertion_gate）定义。"""

    def __init__(self, model: ChatModel, evidence_budget: int) -> None:
        self._model: ChatModel = model
        self._budget: int = evidence_budget

    async def audit(
        self,
        text: str,
        question: str,
        passages: Sequence[Passage],
        *,
        tracer: EventSink,
        record_excerpts: Mapping[str, str],
    ) -> AssertionAudit:
        """一次完整校验；任一环节失败由 audit_response 内的 fail-open 兜底。"""
        chat = ChatClient(self._model, tracer)
        return await audit_response(
            chat,
            text=text,
            question=question,
            passages=passages,
            record_excerpts=record_excerpts,
            evidence_budget=self._budget,
        )
