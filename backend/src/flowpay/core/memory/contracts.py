"""跨会话排查记忆：策略、数据结构与能力协议（specs/004）。

只存排查结论：会话收尾确定性抽取，新会话按结构化键精确召回、语义相似度兜底。
s09 式用户偏好/反馈等类别与 LLM 蒸馏均在范围外（specs/004 spec.md）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from flowpay.core.json import JsonObject

# 单条记忆正文的字符上限（specs/004 data-model.md，模块常量不进配置面）。
NOTE_BODY_CAP = 4000


@dataclass(frozen=True)
class MemoryPolicy:
    """记忆链路策略；recall_char_budget > 0 才启用，缺省 0 = 关闭 = 现行为。

    语义兜底需要嵌入配置：semantic_limit > 0 而 embedding 未装配时应在启动前
    失败（bootstrap 校验），而不是运行到一半才发现检索不出向量。
    """

    recall_char_budget: int
    # 语义兜底召回的候选数；0 = 仅键匹配，不发生任何嵌入调用。
    semantic_limit: int = 0
    # 记忆总量封顶；超出按 created_at 最旧淘汰。
    max_notes: int = 200


@dataclass(frozen=True)
class MemoryNote:
    """一条排查结论记忆；来源会话删除不联动删除本记录（specs/004 spec.md）。"""

    note_id: str
    session_id: str
    workflow: str
    created_at: str
    # 结构化键（casefold 归一、有序）：case 值与查询目标里出现的记录编号。
    keys: list[str]
    conditions: JsonObject
    body: str
    embedding: list[float] | None
    body_chars: int


@dataclass(frozen=True)
class RecalledNote:
    """召回结果：键命中 score 固定 1.0，语义命中为余弦分数。"""

    note_id: str
    body: str
    score: float


class MemoryRecall(Protocol):
    """工作流可用的记忆召回能力；实现方负责事件记录与失败降级。"""

    async def recall(self, keys: list[str], query: str, *, limit: int = 3) -> list[RecalledNote]:
        """按结构化键精确匹配召回记忆；实现不得抛异常阻断本轮。"""
        ...
