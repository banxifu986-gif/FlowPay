# 回归测试：混合检索的 RRF 融合与关键词模式转义（specs/006 US4）。
from flowpay.core.knowledge.contracts import Passage, PassageSource
from flowpay.core.knowledge.store import (
    keyword_like_pattern,
    reciprocal_rank_fusion,
)


def passage(doc_id: str, chunk_no: int, score: float) -> Passage:
    """构造带完整溯源的最小片段；融合只依赖 id 与 source。"""
    return Passage(
        id=f"doc:{doc_id}#c{chunk_no:03}",
        title=doc_id,
        text=doc_id,
        score=score,
        source=PassageSource(
            collection_id="c",
            doc_id=doc_id,
            chunk_no=chunk_no,
            path=f"{doc_id}.md",
            sha256="a" * 64,
            locator={"heading": doc_id},
            metadata={"audience": "external"},
        ),
    )


def testreciprocal_rank_fusions_and_deduplicates_by_passage_id() -> None:
    """两路都命中的片段累加分数；同 id 只留一份，溯源字段原样保留。"""
    vector = [passage("alpha", 1, 0.9), passage("beta", 1, 0.8)]
    keyword = [passage("beta", 1, 0.7), passage("gamma", 1, 0.6)]
    fused = reciprocal_rank_fusion(vector, keyword, limit=2, rrf_k=60)
    assert [item.id for item in fused] == ["doc:beta#c001", "doc:alpha#c001"]
    beta = fused[0]
    assert beta.source.path == "beta.md"
    assert beta.source.metadata == {"audience": "external"}
    assert beta.score == 1 / 62 + 1 / 61


def testkeyword_like_pattern_escapes_wildcards_but_keeps_text() -> None:
    """用户输入的 % 与 _ 按字面转义，不改变匹配范围。"""
    assert keyword_like_pattern("pay_bg_001_0003") == "%pay\\_bg\\_001\\_0003%"
    assert keyword_like_pattern("50%") == "%50\\%%"
