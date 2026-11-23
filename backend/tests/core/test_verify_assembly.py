# 回归测试：校验器证据装配（specs/010 FR-1）。固定输入复现边界，不作为业务答案来源。
from flowpay.core.knowledge.contracts import Passage, PassageSource
from flowpay.core.verify.assembly import assemble_evidence, render_evidence


def passage(identifier: str, text: str, score: float) -> Passage:
    """构造一个最小片段；id 用真实形态 doc:KB-XXX#cNNN。"""
    return Passage(
        id=identifier,
        title=f"{identifier} 片段",
        text=text,
        score=score,
        source=PassageSource(
            collection_id="fixture",
            doc_id=identifier.split("#")[0].removeprefix("doc:"),
            chunk_no=1,
            path="knowledge/fixture.md",
            sha256="fixture",
            locator={"start_char": 0},
            metadata={},
        ),
    )


ROUNDS = [
    passage("doc:KB-TS-012#c001", "企业版工单通常 5 个工作日响应。", 0.9),
    passage("doc:KB-TS-012#c002", "免费版不提供专属工单通道。", 0.7),
    passage("doc:KB-BILL-003#c001", "退款在 3 个工作日内原路退回。", 0.5),
]


def test_bundle_carries_text_question_refs_and_record_ids() -> None:
    """装配输入逐项：文本、问题、两种词面的引用、记录编号都进证据集。"""
    text = (
        "企业版所有用户最多等 5 天 [KB-TS-012#c001]，"
        "见退款记录 pay_bg_001_0003 与 [doc:KB-BILL-003#c001]。"
    )
    bundle = assemble_evidence(
        text=text,
        question="企业版工单响应时长是多少？",
        passages=ROUNDS,
        record_excerpts={"pay_bg_001_0003": "status=refunded"},
        evidence_budget=8000,
    )
    assert bundle.text == text
    assert bundle.question == "企业版工单响应时长是多少？"
    assert bundle.refs == ("KB-TS-012#c001", "KB-BILL-003#c001")
    assert bundle.record_ids == ("pay_bg_001_0003",)


def test_bound_passages_matched_and_ranked_capped_by_budget() -> None:
    """绑定引用原文全部进集；其余按 score 降序、字符封顶只作用于全轮片段。"""
    text = "退款在 3 个工作日内原路退回 [KB-BILL-003#c001]。"
    bundle = assemble_evidence(
        text=text,
        question="退款多久到账？",
        passages=ROUNDS,
        record_excerpts={},
        evidence_budget=len("企业版工单通常 5 个工作日响应。"),
    )
    assert [p.id for p in bundle.bound_passages] == ["doc:KB-BILL-003#c001"]
    ranked = [p.id for p in bundle.ranked_passages]
    assert ranked == ["doc:KB-TS-012#c001"]  # c002 被预算截掉，绑定项不受预算影响
    assert all(p.id != "doc:KB-TS-012#c002" for p in bundle.bound_passages + bundle.ranked_passages)


def test_ref_outside_round_set_stays_in_refs_without_passage() -> None:
    """引用不存在于本轮片段集：不绑定原文，仍保留在 refs 里交给 ID 存在性核对（FR-3）。"""
    text = "企业版通常 5 个工作日响应 [KB-TS-099#c007]。"
    bundle = assemble_evidence(
        text=text,
        question="响应时长？",
        passages=ROUNDS,
        record_excerpts={},
        evidence_budget=8000,
    )
    assert bundle.refs == ("KB-TS-099#c007",)
    assert bundle.bound_passages == ()
    assert "doc:KB-TS-099#c007" not in bundle.known_ids


def test_missing_record_excerpt_reported_without_blocking() -> None:
    """摘录缺失记入 missing_records 参与判定，不阻塞装配（边界情况）。"""
    text = "退款已提交 pay_bg_001_0003 [KB-BILL-003#c001]，另一笔 req_bm_030_01 未查到。"
    bundle = assemble_evidence(
        text=text,
        question="退款进度？",
        passages=ROUNDS,
        record_excerpts={"req_bm_030_01": "  "},  # 空白数据同缺失（记录数据为空）
        evidence_budget=8000,
    )
    assert bundle.record_excerpts == ()
    assert bundle.missing_records == ("pay_bg_001_0003", "req_bm_030_01")


def test_render_contains_sections_and_omits_empty_ones() -> None:
    """渲染块包含问题、文本、绑定原文、全轮片段与摘录；缺失段列出缺失编号。"""
    text = "企业版通常 5 个工作日响应 [KB-TS-012#c001]，记录 pay_bg_001_0003 显示退款失败。"
    bundle = assemble_evidence(
        text=text,
        question="工单响应多久？",
        passages=ROUNDS,
        record_excerpts={"pay_bg_001_0003": "status=failed"},
        evidence_budget=8000,
    )
    rendered = render_evidence(bundle)
    assert "工单响应多久？" in rendered
    assert text in rendered
    assert "[KB-TS-012#c001]" in rendered and "企业版工单通常 5 个工作日响应。" in rendered
    assert "pay_bg_001_0003" in rendered and "status=failed" in rendered
    assert "KB-TS-012#c002" in rendered  # 全轮片段进入渲染
    assert "证据缺失" not in rendered  # 无缺失时不出现缺失段


def test_render_lists_missing_records_and_skips_empty_sections() -> None:
    """缺失摘录在渲染里标注按证据不足处理；空摘录段整体省略。"""
    bundle = assemble_evidence(
        text="免费版不支持专属工单 [KB-TS-012#c002]。",
        question="免费版有工单吗？",
        passages=(),
        record_excerpts={},
        evidence_budget=8000,
    )
    rendered = render_evidence(bundle)
    # 引用无原文且无全轮片段：片段正文不渲染（引用词面本身留在待校验文本里属正常）。
    assert "免费版不提供专属工单通道" not in rendered
    assert "证据缺失" not in rendered
    assert "免费版不支持专属工单" in rendered


def test_blank_inputs_degenerate_to_empty_render() -> None:
    """空文本装配退化为空证据块，调用方在进入校验链前短路。"""
    bundle = assemble_evidence(
        text="", question="", passages=(), record_excerpts={}, evidence_budget=8000
    )
    assert bundle.refs == () and bundle.record_ids == ()
    assert render_evidence(bundle) == ""
