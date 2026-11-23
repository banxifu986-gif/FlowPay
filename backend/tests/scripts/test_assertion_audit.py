"""assertion_audit：010 旁路回放驱动与断点归类的受控回归（specs/010 T009-T011）。

只测外部可见行为：事件恢复（草稿/证据/摘录/已见编号）、校验链重放与在线
citation_check 同构、fixture 桩判定、逐题 JSONL 形态。全部本地 fixture，零
模型调用（SC-4 的机制预演）。
"""

import json
import sqlite3
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from flowpay.core.event_log.types import Event
from flowpay.core.json import JsonObject, parse_json, require_object
from flowpay.core.knowledge.contracts import Passage, PassageSource
from flowpay.core.verify import Assertion, AssertionVerdict, Verdict

# scripts 不是 backend 安装包的一部分：宿主 pytest 单文件快跑需要仓库根在
# sys.path（容器全量从仓库根跑时本就可 import，注入是幂等兜底）。
ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.benchmark import assertion_audit  # noqa: E402
from scripts.benchmark.assertion_audit import (  # noqa: E402
    AuditInput,
    AuditRow,
    BreakpointContext,
    ExpectedEvidence,
    FixtureVerifier,
    ReplayCase,
    audit_case,
    breakpoint_entries,
    breakpoint_summary,
    classify_breakpoint,
    collateral_baseline,
    draft_of,
    load_fixture,
    match_sessions,
    passage_headings_of,
    record_excerpts_of,
    replay_chain,
    required_assertions,
    restore_case,
    seen_ids,
    verdict_payload,
    write_verdicts,
)
from scripts.benchmark.attribution import Row  # noqa: E402


# attribution.Row 的最小构造参数；逐题结果行的测试替身。
def row(question_id: str, task_pass: bool = False) -> Row:
    return Row(
        question_id=question_id,
        question_type="simple_retrieval",
        multi_turn=False,
        doc="0/1",
        record="0/0",
        assertion="0/1",
        evidence_pass=False,
        task_pass=task_pass,
        signals=[],
        deviations=[],
    )


def case(
    question_id: str = "FP-BM-SR-0001", initial: str = "查一下 FP-4041 的含义。"
) -> JsonObject:
    """题库行替身：question_id + initial_user_message（会话锚点）。"""
    return {
        "question_id": question_id,
        "question_type": "simple_retrieval",
        "conversation": {"initial_user_message": initial},
    }


def event(kind: str, payload: JsonObject, seq: int = 0) -> Event:
    """事件替身：补齐 Event 协议字段，回放只读 type/payload/seq。"""
    return {
        "schema_version": 1,
        "event_id": f"evt_{seq}",
        "session_id": "session_fixture",
        "seq": seq,
        "run_id": "run_fixture",
        "call_id": None,
        "parent_call_id": None,
        "timestamp": "2026-01-01T00:00:00Z",
        "type": kind,
        "payload": payload,
    }


def answer_call(mode: str = "answer", answer: str = "") -> Event:
    """set_response 决策调用的 model.response 替身。"""
    return event(
        "model.response",
        {
            "completion": {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "function": {
                                        "name": "set_response",
                                        "arguments": json.dumps(
                                            {"mode": mode, "answer": answer}, ensure_ascii=False
                                        ),
                                    }
                                }
                            ],
                        }
                    }
                ]
            },
        },
    )


def passage(
    identifier: str = "doc:KB-TS-012#c001",
    text: str = "FP-4041 表示 Customer 不存在或不可见。",
) -> Passage:
    """证据池片段替身；locator 的 heading 供对齐器使用。"""
    return Passage(
        id=identifier,
        title="测试片段",
        text=text,
        score=0.5,
        source=PassageSource(
            collection_id="fixture",
            doc_id="KB-TS-012",
            chunk_no=1,
            path="knowledge/KB-TS-012.md",
            sha256="0" * 64,
            locator={"heading": "FP-4041 语义"},
            metadata={},
        ),
    )


def passage_json(**overrides: object) -> JsonObject:
    """片段的 JSON 形态（检查点 state.evidence 条目）：Passage 字段全展开。"""
    item = passage()
    payload: JsonObject = {
        "id": item.id,
        "title": item.title,
        "text": item.text,
        "score": item.score,
        "source": {
            "collection_id": item.source.collection_id,
            "doc_id": item.source.doc_id,
            "chunk_no": item.source.chunk_no,
            "path": item.source.path,
            "sha256": item.source.sha256,
            "locator": dict(item.source.locator),
            "metadata": dict(item.source.metadata),
        },
    }
    payload.update(overrides)
    return payload


def build_arm(
    root: Path, session_id: str, events: list[Event], question_id: str = "FP-BM-SR-0001"
) -> Path:
    """构造最小验收臂：multi-agent.jsonl + sessions.sqlite 的 events 表。"""
    (root / "multi-agent").mkdir(parents=True)
    result: JsonObject = {
        "question_id": question_id,
        "question_type": "simple_retrieval",
        "multi_turn": False,
        "doc_hit": 1,
        "doc_total": 1,
        "record_hit": 0,
        "record_total": 0,
        "assertion_hit": 1,
        "assertion_total": 1,
        "negative_hit": 0,
        "negative_total": 1,
        "evidence_pass": True,
        "task_pass": True,
        "seconds": 1.0,
        "deviations": [],
        "signals": [],
        "unverified": 0,
    }
    (root / "multi-agent.jsonl").write_text(
        json.dumps(result, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    db = sqlite3.connect(root / "multi-agent" / "sessions.sqlite")
    db.execute("CREATE TABLE events (session_id TEXT, seq INTEGER, type TEXT, data TEXT)")
    for index, item in enumerate(events):
        db.execute(
            "INSERT INTO events VALUES (?, ?, ?, ?)",
            (session_id, index, item["type"], json.dumps({"payload": item["payload"]})),
        )
    db.commit()
    db.close()
    return root


def test_draft_recovery_and_replay_chain() -> None:
    """synthesize 草稿恢复 + 校验链重放与在线 citation_check 同构。"""
    answer = "FP-4041 的结果是 Customer 不可见。详见 [doc:KB-TS-012#c001]。\n\n该支付已成功。"
    events = [answer_call("answer", answer)]
    found = draft_of(events)
    assert found is not None and found[0] == answer and found[1] == "synthesize_answer"
    # 与在线 audited_response 相同的引用标记校验：标记不跨句，首句无标记被降级，
    # 带标记句原样，空行结构保留——输出与 check_citations 逐字一致。
    text = replay_chain(answer)
    assert text == (
        "未能核实：FP-4041 的结果是 Customer 不可见。"
        "详见 [doc:KB-TS-012#c001]。\n\n"
        "未能核实：该支付已成功。"
    )


def test_draft_recovery_branches() -> None:
    """clarify 终态与轻分支不产出草稿；两者不进 deviations。"""
    assert draft_of([answer_call("clarify")]) == (None, "clarify_terminal")
    direct = event(
        "model.response",
        {"completion": {"choices": [{"message": {"role": "assistant", "content": "直答正文"}}]}},
    )
    assert draft_of([direct]) == (None, "light_branch")


def test_match_sessions_by_initial_message(tmp_path: Path) -> None:
    """首条用户消息按空白归一匹配 initial_user_message；不猜未匹配会话。"""
    arm = build_arm(
        tmp_path,
        "session_a",
        [
            event("session.created", {}),
            event("message.user", {"content": "查一下   FP-4041\n的含义。"}),
            answer_call("answer", "正文"),
        ],
    )
    from scripts.benchmark.assertion_audit import read_session_events

    sessions = read_session_events(arm)
    matched = match_sessions([case(initial="查一下 FP-4041 的含义。")], sessions)
    assert matched == {"FP-BM-SR-0001": "session_a"}
    unmatched = match_sessions([case(initial="另一个问题")], sessions)
    assert unmatched == {}


def test_record_excerpts_and_seen() -> None:
    """记录摘录自 tool.result 恢复（文档工具除外）；已见编号含用户消息。"""
    events = [
        event("message.user", {"content": "支付 pay_x01 哪里失败了"}),
        event(
            "tool.result",
            {
                "name": "query_payments",
                "result": {"rows": [{"payment_id": "pay_x01", "status": "failed"}]},
            },
        ),
        event(
            "tool.result",
            {"name": "search_documents", "result": {"passages": [{"id": "doc:KB-1#c001"}]}},
        ),
    ]
    excerpts = record_excerpts_of(events)
    assert "pay_x01" in excerpts and "payment_id" in excerpts["pay_x01"]
    assert seen_ids(events) == {"pay_x01"}


def test_restore_case_fills_passages_and_deviations() -> None:
    """restore_case：synthesize 题恢复草稿与证据；轻分支不记偏差；缺会话记偏差。"""
    answer = "结论见 [doc:KB-TS-012#c001]。"
    events = [
        event("message.user", {"content": "查 FP-4041"}),
        event(
            "run.finished",
            {"status": "completed", "state": {"evidence": [passage_json()]}},
        ),
        answer_call("answer", answer),
    ]
    restored = restore_case(row("FP-BM-SR-0001"), case(), "session_a", events)
    assert restored.branch == "synthesize_answer" and restored.draft == answer
    assert restored.text == replay_chain(answer)
    assert restored.passages and restored.passages[0].id == "doc:KB-TS-012#c001"
    assert restored.deviations == []
    light = restore_case(row("FP-BM-SR-0001"), case(), "session_a", events[:2])
    assert light.branch == "light_branch" and light.draft is None and light.deviations == []
    missing = restore_case(row("FP-BM-SR-0001"), case(), None, None)
    assert missing.branch == "session_missing" and "session_missing" in missing.deviations


async def test_fixture_verifier_word_and_id_rules() -> None:
    """fixture 桩的两级判定：词面支持（评测口径）与 ID 存在性（FR-3 口径）。"""
    verifier = FixtureVerifier()
    supported = await verifier.audit(
        AuditInput(
            text="FP-4041 的含义是 Customer 不存在或不可见，见 [doc:KB-TS-012#c001]。",
            question="FP-4041 是什么？",
            passages=(passage(),),
            records={},
        )
    )
    assert [v.verdict for v in supported] == ["supported"]
    missing = await verifier.audit(
        AuditInput(
            text="FP-4041 的含义是错误码，见 [doc:KB-404#c009]。",
            question="FP-4041 是什么？",
            passages=(passage(),),
            records={"pay_x01": "payment_id=pay_x01"},
        )
    )
    assert [v.verdict for v in missing] == ["insufficient"]
    assert missing[0].assertion.refs == ("doc:KB-404#c009",)


async def test_fixture_verifier_record_missing_is_insufficient() -> None:
    """断言引用的记录编号无摘录 → 证据不足（摘录缺失按证据缺失参与判定）。"""
    verifier = FixtureVerifier()
    verdicts = await verifier.audit(
        AuditInput(
            text="该笔支付的结果是退款完成，记录 pay_y01 可查。",
            question="支付结果？",
            passages=(passage(),),
            records={},
        )
    )
    assert [v.verdict for v in verdicts] == ["insufficient"]
    assert verdicts[0].assertion.record_ids == ("pay_y01",)


async def test_fixture_override_replaces_word_rule(tmp_path: Path) -> None:
    """fixture 覆写优先于词面规则：partial + overreach + revision 原样生效。"""
    fixture = load_fixture(
        write_override(
            tmp_path / "fixture.json",
            {
                "FP-BM-SR-0001": [
                    {
                        "match": "FP-4041 的含义是",
                        "verdict": "partial",
                        "overreach": {"dimension": "对象", "note": "原文只适用于生产环境"},
                        "revision": "FP-4041 在生产环境表示",
                    }
                ]
            },
        )
    )
    verifier = FixtureVerifier(fixture)
    verdicts = await verifier.audit(
        AuditInput(
            text="FP-4041 的含义是 Customer 不存在，见 [doc:KB-TS-012#c001]。",
            question="FP-4041？",
            passages=(passage(),),
            records={},
            question_id="FP-BM-SR-0001",
        )
    )
    assert verdicts[0].verdict == "partial"
    assert verdicts[0].overreach is not None and verdicts[0].overreach.dimension == "对象"
    assert verdicts[0].revision == "FP-4041 在生产环境表示"
    # 未命中的断言仍走词面规则。
    other = await verifier.audit(
        AuditInput(
            text="FP-4041 的含义是 Customer 不存在，见 [doc:KB-TS-012#c001]。",
            question="FP-4041？",
            passages=(passage(),),
            records={},
            question_id="FP-BM-OTHER",
        )
    )
    assert other[0].verdict == "supported"


def write_override(path: Path, payload: JsonObject) -> Path:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


async def test_audit_case_marks_invented_ids() -> None:
    """终态草稿出现轮内未见编号 → deviation（编造核对重放校验）。"""
    verifier = FixtureVerifier()
    replay = ReplayCase(
        question_id="FP-BM-SR-0001",
        question_type="simple_retrieval",
        multi_turn=False,
        task_pass=True,
        evidence_pass=True,
        session_id="s1",
        question="问题",
        branch="synthesize_answer",
        draft="结论是 pay_ghost_1 失败。",
        text=replay_chain("结论是 pay_ghost_1 失败。"),
        passages=(passage(),),
        records={"pay_x01": "行"},
        seen=frozenset({"pay_x01"}),
    )
    audit = await audit_case(replay, verifier)
    assert audit.deviations == ("invented:pay_ghost_1",)


def test_verdict_payload_matches_contract() -> None:
    """判定条目 JSON 与 verdict-schema 的字段对齐：text/refs/record_ids/verdict。"""
    payload = verdict_payload(
        AssertionVerdict(
            assertion=Assertion(text="句子", refs=("doc:KB-1#c001",), record_ids=("pay_1",)),
            verdict="contradicted",
        )
    )
    assert payload["verdict"] == "contradicted"
    assert payload["refs"] == ["doc:KB-1#c001"]
    assert "overreach" not in payload


async def test_write_verdicts_jsonl(tmp_path: Path) -> None:
    """JSONL 一行一题：branch/verdicts/deviations 全部落位。"""
    rows = [
        await audit_case(
            ReplayCase(
                question_id="FP-BM-SR-0001",
                question_type="simple_retrieval",
                multi_turn=False,
                task_pass=True,
                evidence_pass=True,
                session_id="s1",
                question="q",
                branch="synthesize_answer",
                draft="见 [doc:KB-TS-012#c001]。",
                text=replay_chain("见 [doc:KB-TS-012#c001]。"),
                passages=(passage(),),
                records={},
                seen=frozenset(),
            ),
            FixtureVerifier(),
        )
    ]
    out = tmp_path / "verdicts.jsonl"
    write_verdicts(out, rows)
    line = require_object(parse_json(out.read_text(encoding="utf-8")))
    assert line["question_id"] == "FP-BM-SR-0001" and line["branch"] == "synthesize_answer"
    assert line["verdicts"] == []


def audit_row(
    question_id: str,
    verdicts: tuple[AssertionVerdict, ...],
    *,
    task_pass: bool = True,
    required: tuple[str, ...] = (),
) -> AuditRow:
    """误伤基线测试用的判定行替身。"""
    return AuditRow(
        question_id=question_id,
        question_type="simple_retrieval",
        multi_turn=False,
        task_pass=task_pass,
        evidence_pass=True,
        session_id="s1",
        branch="synthesize_answer",
        verdicts=verdicts,
        deviations=(),
        required=required,
    )


def test_collateral_baseline_counts_and_samples() -> None:
    """通过题的 partial 判定句覆盖必需断言 → 计入误伤；失败题不参与。"""
    required = ("FP-4041 表示 Customer 不存在或不可见。",)
    harmed = audit_row(
        "FP-BM-SR-0001",
        (
            AssertionVerdict(
                assertion=Assertion(
                    text="FP-4041 的含义是 Customer 不存在，见 [doc:KB-TS-012#c001]。",
                ),
                verdict="partial",
            ),
        ),
        required=required,
    )
    clean = audit_row(
        "FP-BM-SR-0002",
        (
            AssertionVerdict(
                assertion=Assertion(text="见 [doc:KB-TS-012#c001]。"), verdict="partial"
            ),
        ),
        required=required,
    )
    failing = audit_row(
        "FP-BM-SR-0003",
        (
            AssertionVerdict(
                assertion=Assertion(text="FP-4041 的含义是 Customer 不存在。"), verdict="partial"
            ),
        ),
        task_pass=False,
        required=required,
    )
    baseline = collateral_baseline([harmed, clean, failing])
    assert baseline.passed_required_total == 2
    assert baseline.harmed == 1
    assert baseline.samples[0].question_id == "FP-BM-SR-0001"
    assert baseline.samples[0].matched == required
    assert abs(baseline.rate - 0.5) < 1e-9


def test_collateral_baseline_zero_when_all_supported() -> None:
    """通过题零误伤形态：判定全 supported → 比例 0（go 侧理想值）。"""
    required = ("FP-4041 表示 Customer 不存在或不可见。",)
    clean = audit_row(
        "FP-BM-SR-0001",
        (
            AssertionVerdict(
                assertion=Assertion(text="FP-4041 表示 Customer 不存在。"), verdict="supported"
            ),
        ),
        required=required,
    )
    baseline = collateral_baseline([clean])
    assert baseline.passed_required_total == 1 and baseline.harmed == 0 and baseline.rate == 0.0


def test_required_assertions_from_case() -> None:
    """题库必需断言文本提取：text 字段逐条展开。"""
    blueprint: JsonObject = {
        "question_id": "FP-BM-SR-0001",
        "ground_truth": {
            "required_assertions": [{"text": "断言一。"}, {"text": "断言二。"}],
            "negative_assertions": [{"text": "不得认定：断言三。"}],
        },
    }
    assert required_assertions(blueprint) == ("断言一。", "断言二。")


def verdict(text: str, verdict_kind: Verdict) -> AssertionVerdict:
    """归类规则测试用的判定替身。"""
    return AssertionVerdict(assertion=Assertion(text=text), verdict=verdict_kind)


def breakpoint_context(expected: ExpectedEvidence | None = None) -> BreakpointContext:
    """归类上下文替身：证据池已含"FP-4041 语义"小节 + 已见记录 pay_x01。"""
    headings, ref_headings = passage_headings_of((passage(),))
    return BreakpointContext(
        passage_headings=headings,
        seen_records=frozenset({"pay_x01"}),
        ref_headings=ref_headings,
        expected=expected,
    )


def test_classify_breakpoint_rules() -> None:
    """四类断点规则逐条：partial/insufficient×召回×绑定/contradicted。"""
    expected = ExpectedEvidence(
        headings=frozenset({"FP-4041 语义"}), records=frozenset({"pay_x01"})
    )
    context = breakpoint_context(expected)
    assert classify_breakpoint(verdict("句", "partial"), context) == (
        "overreach",
        "partial",
        None,
    )
    assert classify_breakpoint(verdict("句", "contradicted"), context)[0] == "contradicted"
    # 期望证据本轮没取到 → 没召回。
    missed = breakpoint_context(
        ExpectedEvidence(headings=frozenset({"别的小节"}), records=frozenset({"pay_z99"}))
    )
    assert classify_breakpoint(verdict("句", "insufficient"), missed) == (
        "not_recalled",
        "insufficient∧期望证据∉本轮证据集",
        False,
    )
    # 期望证据在集但断言没绑它 → 绑错。
    unbound = breakpoint_context(expected)
    assert classify_breakpoint(verdict("句", "insufficient"), unbound) == (
        "mis_bound",
        "insufficient∧期望证据∈集∧未绑定",
        True,
    )
    # 绑定正确仍证据不足：四类之外，不归类。
    bound = BreakpointContext(
        passage_headings=unbound.passage_headings,
        seen_records=unbound.seen_records,
        ref_headings=unbound.ref_headings,
        expected=expected,
    )
    binding = AssertionVerdict(
        assertion=Assertion(text="句", refs=("doc:KB-TS-012#c001",), record_ids=("pay_x01",)),
        verdict="insufficient",
    )
    assert classify_breakpoint(binding, bound)[0] == ""
    # supported 不进四类。
    assert classify_breakpoint(verdict("句", "supported"), context)[0] == ""


def test_classify_breakpoint_without_expected() -> None:
    """无题库对照（数据集缺失）时不猜归类：insufficient 不进四类。"""
    context = breakpoint_context(None)
    assert classify_breakpoint(verdict("句", "insufficient"), context) == (
        "",
        "no_expected_evidence",
        None,
    )


def test_breakpoint_summary_aggregates() -> None:
    """汇总聚合：四类计数与矛盾档占比来自逐题归类。"""
    rows = [
        audit_row(
            "FP-BM-SR-0001",
            (verdict("句一", "partial"), verdict("句二", "insufficient")),
        ),
        audit_row(
            "FP-BM-SR-0002",
            (verdict("句三", "contradicted"), verdict("句四", "supported")),
        ),
    ]
    expected = ExpectedEvidence(headings=frozenset({"别的小节"}))
    classified = [
        replace(row, breakpoints=breakpoint_entries(row, BreakpointContext(expected=expected)))
        for row in rows
    ]
    summary = breakpoint_summary(classified)
    assert summary.counts == {
        "overreach": 1,
        "not_recalled": 1,
        "mis_bound": 0,
        "contradicted": 1,
    }
    assert abs(summary.contradiction_share - 1 / 3) < 1e-9
    assert {entry.question_id for entry in summary.samples} == {
        "FP-BM-SR-0001",
        "FP-BM-SR-0002",
    }


# ---- P1-1 清偿回归：恢复层以臂结果题集为权威（审查 2026-11-23）----


def test_replay_arm_all_uses_arm_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--all（per_type=0）恢复臂内全部题，不因题库 300 题的缺题报错。

    R96B2 臂只含 R96B 96 题子集，题库 blueprints() 是 300 全量——恢复层以臂
    行序为事实源，题库只提供 blueprint 侧（修复前 --all 直跑必崩）。
    """
    arm = build_arm(tmp_path, "session_fixture", [])
    monkeypatch.setattr(
        assertion_audit,
        "blueprints",
        lambda: [case("FP-BM-SR-0001"), case("FP-BM-SR-0065", initial="另一题。")],
    )
    cases = assertion_audit.replay_arm(arm, 0)
    assert [item.question_id for item in cases] == ["FP-BM-SR-0001"]


def test_replay_arm_questions_missing_in_arm_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """显式题单内臂缺题保留报错：题单指错题号要暴露，不静默跳过。"""
    arm = build_arm(tmp_path, "session_fixture", [])
    monkeypatch.setattr(
        assertion_audit,
        "blueprints",
        lambda: [case("FP-BM-SR-0001"), case("FP-BM-SR-0065", initial="另一题。")],
    )
    with pytest.raises(SystemExit, match="臂结果缺题单题目"):
        assertion_audit.replay_arm(
            arm, 1, questions=["FP-BM-SR-0001", "FP-BM-SR-0065"]
        )


def test_replay_arm_questions_missing_in_library_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """题单题不在题库报错：blueprint 侧字段（必需断言/期望证据）无从查询。"""
    arm = build_arm(tmp_path, "session_fixture", [], question_id="FP-BM-SR-0065")
    monkeypatch.setattr(assertion_audit, "blueprints", lambda: [case("FP-BM-SR-0001")])
    with pytest.raises(ValueError, match="不在题库"):
        assertion_audit.replay_arm(arm, 1, questions=["FP-BM-SR-0065"])


def test_replay_arm_sample_arm_gap_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """抽样口径（per_type>0）抽出的题臂缺失仍显式报错：数据不一致要暴露。"""
    arm = build_arm(tmp_path, "session_fixture", [])
    monkeypatch.setattr(
        assertion_audit,
        "blueprints",
        lambda: [case("FP-BM-SR-0001"), case("FP-BM-SR-0065", initial="另一题。")],
    )
    with pytest.raises(ValueError, match="结果文件缺题"):
        assertion_audit.replay_arm(arm, 2)
