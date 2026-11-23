# 回归测试：批量语义校验调用（specs/010 FR-3/FR-4）。固定输入复现边界，不作为业务答案来源。
import pytest

from flowpay.core.llm.chat.client import ChatClient
from flowpay.core.verify.assembly import assemble_evidence
from flowpay.core.verify.contracts import Assertion
from flowpay.core.verify.verifier import verify_assertions
from tests.core.verify_fixtures import ROUNDS, RecordingSink, ScriptedModel, completion, json_payload

DRAFT = "企业版所有用户最多等 5 天 [KB-TS-012#c001]。"

# 三类越界样例（spec SC-3）：适用对象扩大 /「通常」→保证 /「工作日」丢失。
OVERREACH_SAMPLES = [
    {
        "text": "企业版所有用户最多等 5 天 [KB-TS-012#c001]。",
        "verdict": "partial",
        "overreach": {"dimension": "对象", "note": "原文限企业版工单，断言扩大到所有用户"},
        "revision": "企业版工单通常 5 个工作日响应 [KB-TS-012#c001]。",
    },
    {
        "text": "平台保证工单 5 个工作日内响应 [KB-TS-012#c001]。",
        "verdict": "partial",
        "overreach": {"dimension": "条件", "note": "原文是「通常」，不是保证"},
        "revision": "平台工单通常 5 个工作日响应 [KB-TS-012#c001]。",
    },
    {
        "text": "企业版工单最多等 5 天 [KB-TS-012#c001]。",
        "verdict": "partial",
        "overreach": {"dimension": "时间", "note": "原文是「5 个工作日」，断言丢了工作日限定"},
        "revision": "企业版工单通常 5 个工作日响应 [KB-TS-012#c001]。",
    },
]


def bundle_with(text: str = DRAFT, rounds: tuple = ROUNDS):
    """装配一个默认证据集（摘录映射随调用单独传，核对口径按断言绑定）。"""
    return assemble_evidence(
        text=text,
        question="企业版工单响应时长是多少？",
        passages=rounds,
        record_excerpts={},
        evidence_budget=8000,
    )


def verifier_with(payload: object | Exception) -> tuple[ChatClient, ScriptedModel]:
    """装配脚本化校验模型。"""
    model = ScriptedModel(payload if isinstance(payload, Exception) else completion(payload))
    return ChatClient(model, RecordingSink()), model


async def test_batch_verdicts_aligned_by_text_echo() -> None:
    """四档判定按 text 回传对齐；overreach/revision/conflict 完整解析。"""
    assertions = (
        Assertion(text="企业版所有用户最多等 5 天 [KB-TS-012#c001]。", refs=("KB-TS-012#c001",)),
        Assertion(
            text="退款超时未退回 pay_bg_001_0003 [KB-BILL-003#c001]。",
            refs=("KB-BILL-003#c001",),
            record_ids=("pay_bg_001_0003",),
        ),
        Assertion(text="免费版也有专属工单通道。", refs=("KB-TS-012#c002",)),
    )
    payload = {
        "verdicts": [
            OVERREACH_SAMPLES[0],
            {
                "text": "退款超时未退回 pay_bg_001_0003 [KB-BILL-003#c001]。",
                "verdict": "contradicted",
                "conflict": {
                    "sources": ["KB-BILL-003#c001", "pay_bg_001_0003"],
                    "point": "原文说 3 个工作日退回，记录显示退款超时未退",
                },
            },
            {"text": "免费版也有专属工单通道。", "verdict": "insufficient"},
        ]
    }
    chat, _ = verifier_with(json_payload(payload))
    verification = await verify_assertions(
        chat, bundle=bundle_with(rounds=ROUNDS), record_excerpts={"pay_bg_001_0003": "status=timeout"}, assertions=assertions
    )
    assert [v.verdict for v in verification.verdicts] == ["partial", "contradicted", "insufficient"]
    partial = verification.verdicts[0]
    assert partial.overreach is not None and partial.overreach.dimension == "对象"
    assert partial.revision == OVERREACH_SAMPLES[0]["revision"]
    conflict = verification.verdicts[1].conflict
    assert conflict is not None and conflict.point.startswith("原文说")
    assert verification.completion is not None


async def test_overreach_dimensions_cover_three_samples() -> None:
    """三类越界样例的 dimension 覆盖 对象/条件/时间（fixture 即 spec SC-3 口径）。"""
    assertions = tuple(
        Assertion(text=sample["text"], refs=("KB-TS-012#c001",)) for sample in OVERREACH_SAMPLES
    )
    chat, _ = verifier_with(json_payload({"verdicts": OVERREACH_SAMPLES}))
    verification = await verify_assertions(chat, bundle=bundle_with(), assertions=assertions, record_excerpts={})
    assert [v.overreach.dimension for v in verification.verdicts] == ["对象", "条件", "时间"]
    assert all(v.verdict == "partial" and v.revision for v in verification.verdicts)


async def test_partial_without_overreach_or_revision_is_insufficient() -> None:
    """partial 缺越界定位或缺收紧表述视同证据不足（spec FR-4 默认假设）。"""
    assertions = (Assertion(text=DRAFT, refs=("KB-TS-012#c001",)),)
    chat, model = verifier_with(
        json_payload({"verdicts": [{"text": DRAFT, "verdict": "partial"}]})
    )
    verdicts = (await verify_assertions(chat, bundle=bundle_with(), assertions=assertions, record_excerpts={})).verdicts
    assert verdicts[0].verdict == "insufficient" and verdicts[0].overreach is None
    chat, _ = verifier_with(
        json_payload(
            {
                "verdicts": [
                    {
                        "text": DRAFT,
                        "verdict": "partial",
                        "overreach": {"dimension": "对象", "note": "越界"},
                    }
                ]
            }
        )
    )
    verdicts = (await verify_assertions(chat, bundle=bundle_with(), assertions=assertions, record_excerpts={})).verdicts
    assert verdicts[0].verdict == "insufficient"  # 缺 revision 同样降档


async def test_unknown_verdict_word_normalized_to_insufficient() -> None:
    """未知判定词归一为证据不足，不炸整链。"""
    assertions = (Assertion(text=DRAFT, refs=("KB-TS-012#c001",)),)
    chat, _ = verifier_with(json_payload({"verdicts": [{"text": DRAFT, "verdict": "true"}]}))
    verdicts = (await verify_assertions(chat, bundle=bundle_with(), assertions=assertions, record_excerpts={})).verdicts
    assert verdicts[0].verdict == "insufficient"


async def test_unalignable_item_skipped_and_assertion_insufficient() -> None:
    """text 对不上的条目跳过；对应断言兜底证据不足。"""
    assertions = (Assertion(text=DRAFT, refs=("KB-TS-012#c001",)),)
    chat, _ = verifier_with(
        json_payload({"verdicts": [{"text": "另一句话。", "verdict": "supported"}]})
    )
    verdicts = (await verify_assertions(chat, bundle=bundle_with(), assertions=assertions, record_excerpts={})).verdicts
    assert verdicts[0].verdict == "insufficient"


async def test_unknown_ref_forces_insufficient_without_model_call() -> None:
    """引用不存在于本轮证据集：直接判证据不足，不经语义校验（FR-3）。"""
    assertions = (
        Assertion(text=DRAFT, refs=("KB-TS-099#c007",)),  # 本轮片段集没有
        Assertion(text="退款在 3 个工作日内原路退回 [KB-BILL-003#c001]。", refs=("KB-BILL-003#c001",)),
    )
    chat, model = verifier_with(json_payload({"verdicts": [
        {"text": "退款在 3 个工作日内原路退回 [KB-BILL-003#c001]。", "verdict": "supported"}
    ]}))
    verification = await verify_assertions(chat, bundle=bundle_with(), assertions=assertions, record_excerpts={})
    assert verification.verdicts[0].verdict == "insufficient"
    assert verification.verdicts[1].verdict == "supported"
    # 语义校验只送未强制的断言：待判定断言清单不含被强制断言
    listing = str(model.requests[0]["messages"][-1]["content"]).split("## 待判定断言")[-1]
    assert "企业版所有用户最多等 5 天" not in listing


async def test_missing_record_excerpt_forces_insufficient() -> None:
    """记录摘录缺失（含空白数据）：涉及该编号的断言按证据不足处置，不阻塞其余断言。"""
    text = "退款已提交 pay_bg_001_0003 [KB-BILL-003#c001]。"
    assertions = (
        Assertion(text=text, refs=("KB-BILL-003#c001",), record_ids=("pay_bg_001_0003",)),
        Assertion(text="退款在 3 个工作日内原路退回 [KB-BILL-003#c001]。", refs=("KB-BILL-003#c001",)),
    )
    bundle = bundle_with(rounds=ROUNDS)  # 摘录缺失
    chat, model = verifier_with(json_payload({"verdicts": [
        {"text": "退款在 3 个工作日内原路退回 [KB-BILL-003#c001]。", "verdict": "supported"}
    ]}))
    verification = await verify_assertions(
        chat, bundle=bundle, assertions=assertions, record_excerpts={}
    )
    assert verification.verdicts[0].verdict == "insufficient"
    assert verification.verdicts[1].verdict == "supported"
    assert len(model.requests) == 1  # 其余断言照常语义校验


async def test_unbound_assertion_forces_insufficient() -> None:
    """无 refs 无 record_ids 的断言没有绑定证据，直接证据不足。"""
    assertions = (Assertion(text="企业版响应很快。"),)
    chat, model = verifier_with(json_payload({"verdicts": []}))
    verdicts = (await verify_assertions(chat, bundle=bundle_with(), assertions=assertions, record_excerpts={})).verdicts
    assert verdicts[0].verdict == "insufficient"
    assert model.requests == []  # 零语义调用


async def test_bad_structure_raises_for_fail_open() -> None:
    """结构坏（非 JSON / 缺 verdicts 清单）上抛，由包装层 fail-open。"""
    assertions = (Assertion(text=DRAFT, refs=("KB-TS-012#c001",)),)
    chat, _ = verifier_with("不是 JSON")
    with pytest.raises(ValueError):
        await verify_assertions(chat, bundle=bundle_with(), assertions=assertions, record_excerpts={})
    chat, _ = verifier_with(json_payload({"kind": "other"}))
    with pytest.raises(ValueError):
        await verify_assertions(chat, bundle=bundle_with(), assertions=assertions, record_excerpts={})


async def test_prompt_carries_assembled_evidence() -> None:
    """校验调用的证据集=装配结果：证据块与断言清单都进 user 消息。"""
    assertions = (Assertion(text=DRAFT, refs=("KB-TS-012#c001",)),)
    chat, model = verifier_with(json_payload({"verdicts": [
        {"text": DRAFT, "verdict": "supported"}
    ]}))
    await verify_assertions(chat, bundle=bundle_with(), assertions=assertions, record_excerpts={})
    user_content = str(model.requests[0]["messages"][-1]["content"])
    assert "企业版工单通常 5 个工作日响应。" in user_content  # 绑定引用原文
    assert "免费版不提供专属工单通道。" in user_content  # 全轮片段
    assert DRAFT in user_content  # 待判定断言清单
