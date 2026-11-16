"""extract_note：确定性抽取的存储条件、三段正文与键提取。"""

from flowpay.core.json import JsonObject
from flowpay.core.memory.contracts import NOTE_BODY_CAP
from flowpay.core.memory.extract import extract_note


def manual_state(actions: list[JsonObject] | None = None) -> JsonObject:
    """manual-loop 检查点形状：case/queried/actions。"""
    return {
        "case": {"known": {"account_id": "acct_bg_001", "payment_id": "pay_bg_001_0003"}},
        "queried": ["query_payments:account_id=acct_bg_001 environment=production"],
        "actions": actions or [],
    }


def multi_state(findings: list[JsonObject] | None = None) -> JsonObject:
    """multi-agent 检查点形状：case/queried/findings。"""
    return {
        "case": {"known": {"account_id": "acct_bg_001"}},
        "queried": [],
        "findings": findings or [],
    }


async def test_manual_loop_actions_extracted_with_three_sections() -> None:
    """manual-loop：三段正文齐全，键含 case 值与目标里的编号。"""
    state = manual_state(
        [
            {
                "tool": "query_payments",
                "target": (
                    "account_id=acct_bg_001 environment=production payment_id=pay_bg_001_0003"
                ),
                "row_count": 1,
            }
        ]
    )
    answer = "支付已成功，投递被 410 拒绝。"
    note = extract_note("session_1", "flowpay-multi-turn-rag", state, answer)
    assert note is not None
    assert note.workflow == "flowpay-multi-turn-rag"
    assert note.conditions == {
        "known": {"account_id": "acct_bg_001", "payment_id": "pay_bg_001_0003"}
    }
    assert "已知条件：" in note.body
    target = "- query_payments account_id=acct_bg_001 environment=production"
    assert f"{target} payment_id=pay_bg_001_0003（1 行）" in note.body
    assert "最终回答：" in note.body
    assert note.keys == ["acct_bg_001", "pay_bg_001_0003"]


async def test_multi_agent_findings_digest_clipped_per_line() -> None:
    """multi-agent：digest 行按行内上限截断，整体结构不变。"""
    state = multi_state(
        [{"tool": "search_documents", "target": "webhook 重试", "rows": 2, "digest": "结论" * 200}]
    )
    note = extract_note("session_1", "flowpay-multi-agent-support", state, "重试策略见文档。")
    assert note is not None
    assert note.body.count("search_documents") == 1
    assert len("结论" * 200) > 200
    assert note.body.index("重试策略见文档") > note.body.index("查询与结论")


async def test_no_progress_returns_none() -> None:
    """零进展（无 actions 且无 findings）不存；空回答也不存。"""
    assert extract_note("s", "w", manual_state(), "有回答但没有查询") is None
    assert extract_note("s", "w", multi_state(), "有回答但没有调查") is None
    state = manual_state([{"tool": "query_payments", "target": "t", "row_count": 0}])
    assert extract_note("s", "w", state, "  ") is None


async def test_body_cap_truncates_with_marker() -> None:
    """正文超 NOTE_BODY_CAP 截断并带显式标记；body_chars 为截断后长度。"""
    state = manual_state([{"tool": "query_payments", "target": "pay_bg_001_0003", "row_count": 1}])
    answer = "长回答" * 3000
    note = extract_note("s", "w", state, answer)
    assert note is not None
    # body_chars 是落盘正文的真实长度：预算正文 + 截断标记（语义见 clip_text）。
    assert note.body_chars == len(note.body)
    assert note.body_chars > NOTE_BODY_CAP
    assert "已截断" in note.body
    # 键提取发生在截断前的原文与回答上，不因截断丢失编号。
    assert note.keys == ["acct_bg_001", "pay_bg_001_0003"]
