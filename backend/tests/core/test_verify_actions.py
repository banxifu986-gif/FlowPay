# 回归测试：处置映射（specs/010 FR-5，research D5）。固定输入复现边界，不作为业务答案来源。
from flowpay.core.verify.actions import apply_actions, downgrade_text, map_actions
from flowpay.core.verify.assembly import assemble_evidence
from flowpay.core.verify.contracts import (
    Assertion,
    AssertionAction,
    AssertionVerdict,
    Conflict,
    Overreach,
    Verdict,
)
from tests.core.verify_fixtures import ROUNDS

EXCERPTS = {"pay_bg_001_0003": "status=timeout"}

DRAFT = (
    "企业版所有用户最多等 5 天 [KB-TS-012#c001]。\n"
    "- 退款已提交 pay_bg_001_0003。\n"
    "退款在 3 个工作日内原路退回 [KB-BILL-003#c001]。"
)


def bundle(text: str = DRAFT):
    """装配默认证据集（含记录摘录）。"""
    return assemble_evidence(
        text=text,
        question="企业版工单响应时长是多少？",
        passages=ROUNDS,
        record_excerpts=EXCERPTS,
        evidence_budget=8000,
    )


def verdict(
    word: Verdict,
    sentence: str,
    *,
    refs: tuple[str, ...] = ("KB-TS-012#c001",),
    revision: str | None = None,
    conflict: Conflict | None = None,
) -> AssertionVerdict:
    """构造一条判定；record_ids 绑定摘录里存在的编号。"""
    return AssertionVerdict(
        assertion=Assertion(text=sentence, refs=refs, record_ids=("pay_bg_001_0003",)),
        verdict=word,
        overreach=Overreach(dimension="对象", note="越界") if word == "partial" else None,
        revision=revision,
        conflict=conflict,
    )


def test_supported_keeps_sentence() -> None:
    """支持→放行。"""
    (action,) = map_actions(
        (verdict("supported", "退款在 3 个工作日内原路退回 [KB-BILL-003#c001]。"),),
        bundle(),
        EXCERPTS,
    )
    assert action.action == "keep" and action.revision is None


def test_partial_replaces_with_revision_in_place() -> None:
    """部分支持→句级替换收紧表述：首处命中，其余文本原样保留。"""
    sentence = "企业版所有用户最多等 5 天 [KB-TS-012#c001]。"
    revision = "企业版工单通常 5 个工作日响应 [KB-TS-012#c001]。"
    (action,) = map_actions((verdict("partial", sentence, revision=revision),), bundle(), EXCERPTS)
    assert action.action == "replace" and action.revision == revision
    assert action.breakpoint_hint == "overreach"
    applied = apply_actions(DRAFT, (action,))
    assert applied.startswith(revision)
    assert "- 退款已提交 pay_bg_001_0003。" in applied
    assert "退款在 3 个工作日内原路退回 [KB-BILL-003#c001]。" in applied


def test_partial_subset_violation_downgrades() -> None:
    """替换句数字越界（原句 ∪ 绑定证据之外的数字）：该句降级不替换。"""
    sentence = "企业版所有用户最多等 5 天 [KB-TS-012#c001]。"
    word = verdict(
        "partial", sentence, revision="企业版工单通常 7 个工作日响应 [KB-TS-012#c001]。"
    )
    (action,) = map_actions((word,), bundle(), EXCERPTS)
    assert action.action == "downgrade"
    assert apply_actions(DRAFT, (action,)) == DRAFT.replace(sentence, downgrade_text(sentence))


def test_partial_new_record_id_downgrades() -> None:
    """替换句引入原句与绑定证据之外的记录编号：越界降级。"""
    sentence = "退款已提交 pay_bg_001_0003。"
    word = verdict(
        "partial",
        sentence,
        refs=("KB-BILL-003#c001",),
        revision="退款已提交 pay_bg_001_0003 并关联 pay_bg_999_9999。",
    )
    (action,) = map_actions((word,), bundle(), EXCERPTS)
    assert action.action == "downgrade"


def test_partial_numbers_from_bound_evidence_allowed() -> None:
    """替换句数字来自绑定证据原文：子集校验放行。"""
    word = verdict(
        "partial",
        "退款已提交 pay_bg_001_0003。",
        refs=("KB-BILL-003#c001",),
        revision="退款已提交 pay_bg_001_0003，3 个工作日内原路退回。",
    )
    (action,) = map_actions((word,), bundle(), EXCERPTS)
    assert action.action == "replace"  # 「3」来自 KB-BILL-003#c001 原文


def test_partial_without_revision_downgrades() -> None:
    """部分支持缺收紧表述：不收紧只降级（FR-4 防线）。"""
    sentence = "企业版所有用户最多等 5 天 [KB-TS-012#c001]。"
    (action,) = map_actions((verdict("partial", sentence),), bundle(), EXCERPTS)
    assert action.action == "downgrade" and action.breakpoint_hint == "overreach"


def test_match_failure_downgrades_instead_of_replace() -> None:
    """原句全文匹配失败（模型改写了句子）：该断言降级不替换，正文不动。"""
    word = verdict(
        "partial",
        "企业版所有用户最多等 5 天 [KB-TS-012#c001]（模型改写版）",
        revision="企业版工单通常 5 个工作日响应 [KB-TS-012#c001]。",
    )
    (action,) = map_actions((word,), bundle(), EXCERPTS)
    assert action.action == "downgrade"
    assert apply_actions(DRAFT, (action,)) == DRAFT


def test_insufficient_downgrades_with_unverified_mark() -> None:
    """证据不足→降级「未能核实：原句」，列表前缀保留。"""
    sentence = "- 退款已提交 pay_bg_001_0003。"
    (action,) = map_actions(
        (verdict("insufficient", sentence, refs=("KB-BILL-003#c001",)),), bundle(), EXCERPTS
    )
    assert action.action == "downgrade"
    assert action.revision == "- 未能核实：退款已提交 pay_bg_001_0003。"
    assert "- 未能核实：退款已提交 pay_bg_001_0003。" in apply_actions(DRAFT, (action,))


def test_contradicted_downgrades_and_keeps_hint() -> None:
    """矛盾→同样降级，断点提示 contradicted；conflict 详情随判定与事件携带。"""
    conflict = Conflict(
        sources=("KB-BILL-003#c001", "pay_bg_001_0003"),
        point="原文说 3 个工作日退回，记录显示超时未退",
    )
    sentence = "退款已提交 pay_bg_001_0003。"
    (action,) = map_actions(
        (verdict("contradicted", sentence, refs=("KB-BILL-003#c001",), conflict=conflict),),
        bundle(),
        EXCERPTS,
    )
    assert action.action == "downgrade" and action.breakpoint_hint == "contradicted"
    assert action.revision == "未能核实：退款已提交 pay_bg_001_0003。"


def test_downgrade_idempotent() -> None:
    """已带未证实口径的句子不再二次改写；重复应用同一处置文本稳定。"""
    marked = "- 未能核实：退款已提交 pay_bg_001_0003。"
    downgraded = DRAFT.replace("- 退款已提交 pay_bg_001_0003。", marked)
    (action,) = map_actions(
        (verdict("insufficient", marked, refs=("KB-BILL-003#c001",)),),
        bundle(downgraded),
        EXCERPTS,
    )
    assert action.action == "keep"  # 幂等：不再二次加口径
    assert apply_actions(downgraded, (action,)) == downgraded
    # 机械层幂等：同一 downgrade 锚点重复应用不再变化（锚点已被替换掉）
    manual = AssertionAction(
        sentence="退款已提交 pay_bg_001_0003。",
        action="downgrade",
        revision="未能核实：退款已提交 pay_bg_001_0003。",
    )
    first = apply_actions(DRAFT, (manual,))
    assert apply_actions(first, (manual,)) == first


def test_apply_replaces_first_occurrence_only() -> None:
    """同一原句出现多处：句级等值匹配命中第一处。"""
    actions = (
        AssertionAction(
            sentence="企业版响应快。",
            action="replace",
            revision="企业版工单通常 5 个工作日响应。",
        ),
    )
    assert (
        apply_actions("企业版响应快。企业版响应快。", actions)
        == "企业版工单通常 5 个工作日响应。企业版响应快。"
    )
