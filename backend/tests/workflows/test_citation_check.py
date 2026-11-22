"""citation_check：结论句引用标记解析与无标记断言降级（specs/009 US3 T016）。

只测外部可见行为：输入草稿文本，输出降级后文本与被降级句清单；
误判边界（疑问句、引导语、请求句、错误码、跨句标记、幂等）逐例固化。
"""

from flowpay.workflows.multi_agent.citation_check import UNVERIFIED_MARK, check_citations


def test_marked_record_id_sentence_passes() -> None:
    """带记录编号的结论句放行，文本原样保留。"""
    text = "该笔支付已退款，记录 pay_bg_001_0003 可查。"
    audit = check_citations(text)
    assert audit.text == text
    assert audit.downgraded == ()


def test_marked_passage_ref_passes() -> None:
    """带片段引用标记的结论句放行。"""
    text = "平台规则允许重试，见 [doc:KB-TS-012#c001]。"
    audit = check_citations(text)
    assert audit.text == text
    assert audit.downgraded == ()


def test_bare_kb_doc_ref_passes() -> None:
    """裸 KB-{模块}-{序号}#c{片段} 引用（模型实际输出的主流格式）放行。

    回归：T026 冒烟实证只认 [doc:...] 会把带 KB 引用的正确断言误降级
    （session_6b11c0 五处"未能核实："前缀污染）。
    """
    text = (
        "**KB-API-014#c006**：FP-4041 的定义涵盖 Payment 不存在、属于其他 Account，或当前不可见。"  # noqa: E501
    )
    audit = check_citations(text)
    assert audit.text == text
    assert audit.downgraded == ()


def test_bold_multi_module_kb_ref_passes() -> None:
    """多模块前缀（KB-REL / KB-BR / KB-TS）与加粗包裹的引用一律放行。"""
    for ref in ("**KB-REL-002#c001**", "KB-BR-007#c002", "`KB-TS-012#c007`"):
        text = f"内部状态不会改写，依据 {ref} 说明版本差异。"
        audit = check_citations(text)
        assert audit.text == text, ref
        assert audit.downgraded == ()


def test_bare_assertion_is_downgraded() -> None:
    """无标记断言句确定性降级为统一未证实口径。"""
    audit = check_citations("该笔支付的结果是退款完成。")
    assert audit.text == f"{UNVERIFIED_MARK}：该笔支付的结果是退款完成。"
    assert audit.downgraded == ("该笔支付的结果是退款完成。",)


def test_only_unmarked_sentences_are_downgraded() -> None:
    """同篇混排时只降级无标记句，带标记句原样。"""
    text = "该笔支付已经退款，见 pay_bg_001_0003。\n\n手续费已全额收取。"
    audit = check_citations(text)
    assert audit.text == (
        f"该笔支付已经退款，见 pay_bg_001_0003。\n\n{UNVERIFIED_MARK}：手续费已全额收取。"
    )
    assert audit.downgraded == ("手续费已全额收取。",)


def test_citation_does_not_span_sentences() -> None:
    """标记不跨句：编号在相邻句不能替本句作保。"""
    audit = check_citations("记录见 pay_bg_001_0003。手续费已全额收取。")
    assert audit.downgraded == ("手续费已全额收取。",)


def test_downgrade_is_idempotent() -> None:
    """已含未证实口径的句子不再重复降级（幂等边界）。"""
    text = f"{UNVERIFIED_MARK}：手续费金额。"
    assert check_citations(text).text == text


def test_unverified_phrases_are_exempt() -> None:
    """既有"未能确认"等未证实口径词的句子不降级。"""
    text = "该笔手续费未能确认，需要进一步核对。"
    assert check_citations(text).downgraded == ()


def test_question_not_downgraded() -> None:
    """疑问句不是断言，不降级。"""
    text = "该笔支付是否已经退款？"
    assert check_citations(text).text == text


def test_lead_in_colon_not_downgraded() -> None:
    """冒号结尾的引导语不是断言句。"""
    text = "已查到的结论："
    assert check_citations(text).text == text


def test_request_sentence_not_downgraded() -> None:
    """请求/指示句（宁可漏判方向）不降级。"""
    text = "如有疑问请联系技术支持。"
    assert check_citations(text).text == text


def test_error_code_is_not_a_citation() -> None:
    """错误码与字段名不是引用标记：无标记的断言句仍降级。"""
    audit = check_citations("接口返回 FP-4021 错误。")
    assert audit.downgraded == ("接口返回 FP-4021 错误。",)
    assert audit.text == f"{UNVERIFIED_MARK}：接口返回 FP-4021 错误。"


def test_bullet_prefix_is_preserved() -> None:
    """列表项降级时保留 Markdown 前缀，未证实口径落在列表项内。"""
    text = "- 该支付失败。\n- 依据：pay_bg_001_0003。"
    audit = check_citations(text)
    assert audit.text == f"- {UNVERIFIED_MARK}：该支付失败。\n- 依据：pay_bg_001_0003。"


def test_line_and_blank_structure_preserved() -> None:
    """行结构与空行原样保留（Markdown 输出不重排）。"""
    text = "结论如下：\n\n该支付已经成功。\n"
    audit = check_citations(text)
    assert audit.text == f"结论如下：\n\n{UNVERIFIED_MARK}：该支付已经成功。\n"


def test_sentence_without_cue_passes_lenient() -> None:
    """无断言线索词的句子按宽松判定放行（宁可漏判不误伤）。"""
    text = "你可以随时在后台查看明细。"
    assert check_citations(text).downgraded == ()


def test_empty_and_separator_only_text() -> None:
    """空文本与纯分隔线不产生降级。"""
    assert check_citations("").downgraded == ()
    assert check_citations("---").text == "---"


def test_unverified_mark_is_the_unified_word() -> None:
    """统一口径词与 FR-7 报表统计共用（T018 数据源）。"""
    assert UNVERIFIED_MARK == "未能核实"
