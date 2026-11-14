"""累积本轮检索到的片段。"""

from __future__ import annotations

import json

from pydantic import TypeAdapter

from flowpay.core.knowledge.contracts import Passage, for_model

# 跨轮携带证据的淘汰上限（specs/003 P2，模块常量不进配置面）。
EVIDENCE_MAX_PASSAGES = 200
EVIDENCE_MAX_CHARS = 40000


class PassageSet:
    """一轮 agent 累积的片段，按 Passage.id 去重。

    检索工具每次把命中加进来，回答随附的证据清单和离线复核都从这里取原文。
    片段不可变，因此登记后直接共享同一个对象；调用方决定它能跨多少轮保留。
    """

    def __init__(self) -> None:
        """新建空集合；恢复检查点请使用 restore。"""
        self.passages: dict[str, Passage] = {}

    @classmethod
    def restore(cls, value: object, collection_id: str | None) -> PassageSet:
        """从检查点恢复片段。

        这是阶段状态与当前数据版本之间的唯一一道边界：受理本轮时已核对过数据版本，
        这里只在真的读到别的集合的片段时拒绝，避免引用指向另一个版本的原文。
        """
        # 检查点里的片段是 JSON 对象，按 Passage 的字段类型还原；strict 会拒绝 dict 输入。
        passages = TypeAdapter(list[Passage]).validate_python(value)
        if any(passage.source.collection_id != collection_id for passage in passages):
            raise ValueError("Saved evidence belongs to another collection")
        restored = cls()
        restored.add(passages)
        return restored

    def add(self, passages: list[Passage]) -> list[Passage]:
        """登记去重后返回本次命中对应的片段，顺序与传入一致。"""
        result: dict[str, Passage] = {}
        for passage in passages:
            result[passage.id] = self.passages.setdefault(passage.id, passage)
        return list(result.values())

    def all(self) -> list[Passage]:
        """按登记顺序返回全部片段，用于检查点和引用关联。"""
        return list(self.passages.values())

    def prune(self, max_passages: int, max_chars: int) -> int:
        """按登记序保最新淘汰跨轮携带的片段，返回淘汰数（specs/003 FR-12）。

        两个上限取更严者：条数超过 max_passages 或正文合计超过 max_chars 时从
        最旧开始淘汰。只在恢复检查点时调用一次——当轮新增的证据不受影响，
        前端展示取当轮 evidence，同样不受影响。
        """
        kept = list(self.passages.values())
        dropped = 0
        while kept and (
            len(kept) > max_passages or sum(len(passage.text) for passage in kept) > max_chars
        ):
            kept.pop(0)
            dropped += 1
        if dropped:
            self.passages = {passage.id: passage for passage in kept}
        return dropped

    def serialize(self) -> str:
        """序列化交给模型的片段；溯源信息留在本对象内，供引用展开和事件日志使用。"""
        return json.dumps(for_model(self.all()), ensure_ascii=False)
