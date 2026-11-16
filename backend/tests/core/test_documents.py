"""通用 Markdown 能力不要求 FlowPay 的发布范围或文档类型标签。"""

import pytest

from flowpay.core.knowledge.documents import parse_document, split_document


def test_document_parser_preserves_metadata_without_imposing_release_policy() -> None:
    """发布政策由导入脚本控制，核心解析仍可处理其他来源的文档元数据。"""
    content = "---\ndoc_id: note\ntitle: 笔记\nindexing: exclude\n---\n## 内容\n可解析的原文。\n"
    document, start = parse_document("notes/example.md", content)
    chunks = split_document(document, start, 1000)
    assert document.metadata["indexing"] == "exclude"
    assert "doc_type" not in document.metadata
    assert chunks[0].content.endswith("## 内容\n可解析的原文。\n")


def test_audience_defaults_to_external_and_rejects_other_values() -> None:
    """文档可见性缺省 external（specs/005 US3）：既有文档先不用逐篇补元数据。"""
    content = "---\ndoc_id: note\ntitle: 笔记\n---\n## 内容\n可解析的原文。\n"
    document, _ = parse_document("notes/example.md", content)
    assert document.metadata["audience"] == "external"
    header = "---\ndoc_id: note\ntitle: 笔记\naudience: "
    body = "\n---\n## 内容\n可解析的原文。\n"
    internal, _ = parse_document("notes/internal.md", header + "internal" + body)
    assert internal.metadata["audience"] == "internal"
    with pytest.raises(ValueError, match="audience"):
        parse_document("notes/bad.md", header + "partner" + body)
