"""benchmark harness：证据对齐、追问判定与六种偏离分类。

这些判断决定报表数字的含义，且都不依赖模型，可以用构造数据钉死。
"""

import json
from collections.abc import Iterator
from typing import cast

import pytest
from scripts.benchmark import conversation, evidence
from scripts.benchmark import run as run_module
from scripts.benchmark.run import Row, overview, supported, violated

from flowpay.core.event_log.types import Event
from flowpay.core.execution.manager import SessionManager
from flowpay.dataset import dataset_root

# 证据对齐要读数据集里的原文；判分与追问判定是纯函数，但为保持一份跳过口径，整份一起跳。
pytestmark = pytest.mark.skipif(
    dataset_root() is None, reason="FLOWPAY_DATASET_ROOT is not configured"
)


def event(kind: str, payload: dict[str, object], run: str = "run_1") -> Event:
    """构造一条最小事件；harness 只读 type、run_id 与 payload。"""
    return {
        "schema_version": 1,
        "event_id": f"evt_{kind}",
        "session_id": "s",
        "seq": 1,
        "run_id": run,
        "call_id": None,
        "parent_call_id": None,
        "timestamp": "2026-09-17T00:00:00Z",
        "type": kind,
        "payload": payload,
    }


def ask_event(missing: list[str], run: str = "run_1", topics: list[str] | None = None) -> Event:
    """构造一次 ask_user 工具调用；topics 是 007 新增的背景主题参数。"""
    arguments: dict[str, object] = {"missing": missing, "question": "？"}
    if topics is not None:
        arguments["topics"] = topics
    return event("tool.started", {"name": "ask_user", "arguments": json.dumps(arguments)}, run)


def decided_event(topics: list[str], conditions: list[str], run: str = "run_1") -> Event:
    """构造一条 synthesize 的澄清决策事件（007 的第二条缺项通道）。"""
    return event(
        "clarification.decided",
        {
            "mode": "clarify",
            "source": "model",
            "topics": topics,
            "conditions": conditions,
            "has_reports": True,
        },
        run,
    )


def test_chunk_alignment_skips_the_title_only_block() -> None:
    """benchmark 把纯标题节算一块，运行时分块器跳过它，因此编号整体差一。

    对齐靠小节标题而不是编号：纯标题块映射到空串，调用方据此知道运行侧没有对应物。
    """
    alignment = evidence.chunk_alignment()
    assert alignment["doc:KB-BR-001#c001"] == ""
    assert alignment["doc:KB-BR-001#c002"] == "适用范围"
    assert alignment["doc:KB-BR-001#c003"] == "Account 是租户边界"


def test_evidence_matches_by_heading_not_by_number() -> None:
    """运行时取到某小节时，应命中 benchmark 里同标题的那一块。"""
    alignment = evidence.chunk_alignment()
    events = [
        event(
            "knowledge.response",
            {
                "results": [
                    {
                        "id": "doc:KB-BR-001#c002",
                        "source": {"locator": {"heading": "文档标题 / Account 是租户边界"}},
                    }
                ]
            },
        )
    ]
    assert "doc:KB-BR-001#c003" in evidence.evidence_ids(events, alignment)


def test_business_identifiers_come_from_tool_results_only() -> None:
    """只有工具真的返回过的编号才算证据；回答里提到的不算。"""
    events = [event("tool.result", {"rows": [{"payment_id": "pay_bg_001_0003"}]})]
    assert "record:pay_bg_001_0003" in evidence.evidence_ids(events, {})


def test_asked_fields_reads_topics_from_ask_user() -> None:
    """ask_user 的 topics 与 missing 一样是缺项声明，都要读出来。"""
    events = [ask_event(["account_id"], topics=["goal", "occurrence_time"])]
    assert conversation.asked_fields(events, "run_1") == [
        "account_id",
        "goal",
        "occurrence_time",
    ]


def test_asked_fields_reads_the_decided_channel() -> None:
    """synthesize 决策事件的 topics/conditions 也是缺项声明。"""
    events = [decided_event(["symptom_details"], ["payment_id"])]
    assert conversation.asked_fields(events, "run_1") == ["symptom_details", "payment_id"]


def test_asked_fields_unions_both_channels_without_duplicates() -> None:
    """两条通道同时存在时取并集，同一字段只出现一次。"""
    events = [
        ask_event(["account_id"], topics=["goal"]),
        decided_event(["goal"], ["account_id"]),
    ]
    assert conversation.asked_fields(events, "run_1") == ["account_id", "goal"]


def test_asked_fields_empty_without_either_channel() -> None:
    """两类事件都没有时返回空列表，不抛异常。"""
    assert conversation.asked_fields([], "run_1") == []
    assert (
        conversation.asked_fields([event("run.finished", {"status": "completed"})], "run_1") == []
    )


def test_decided_events_from_other_runs_are_ignored() -> None:
    """缺项声明只统计本轮；上一轮的决策不能算进这一轮的追问。"""
    events = [
        decided_event(["goal"], [], run="run_0"),
        ask_event(["account_id"], run="run_1"),
    ]
    assert conversation.asked_fields(events, "run_1") == ["account_id"]


def test_topic_fields_map_to_their_own_benchmark_category() -> None:
    """四个背景主题映射到同名 benchmark 类别，不再被映射过滤丢掉。"""
    events = [ask_event([], topics=["goal", "symptom_details", "occurrence_time", "api_version"])]
    signal, categories = conversation.detect(
        "waiting_user", "请补充背景信息？", events, "run_1", set()
    )
    assert signal == "waiting_user"
    assert categories == ["api_version", "goal", "occurrence_time", "symptom_details"]


def test_waiting_user_is_the_precise_clarification_signal() -> None:
    """manual-loop 以 waiting_user 结束本轮时，缺项由 ask_user 的 missing 字段直接给出。"""
    events = [ask_event(["account_id", "payment_id"])]
    signal, categories = conversation.detect("waiting_user", "请问账户？", events, "run_1", set())
    assert signal == "waiting_user"
    assert categories == ["account_context", "identifiers"]


def test_text_fallback_requires_a_real_question_mark() -> None:
    """关键词会误匹配告别语和能力介绍，因此第三级信号必须同时有问号。"""
    closing = "好的，有需要随时找我。以后排查请带上账户 ID 和环境。"
    signal, _ = conversation.detect("completed", closing, [], "run_1", {"account_context"})
    assert signal == "none"
    asking, categories = conversation.detect(
        "completed", "请问是哪个账户？", [], "run_1", {"account_context"}
    )
    assert asking == "text"
    assert categories == ["account_context"]


def test_transcript_records_each_deviation_once() -> None:
    """同种偏离只记一次，报表按题计数。"""
    script = conversation.Transcript()
    script.note("repeated_category")
    script.note("repeated_category")
    script.note("unexpected_category")
    assert script.deviations == ["repeated_category", "unexpected_category"]


def test_assertion_matching_is_specific_enough() -> None:
    """断言判定要能认出改写，也不能对无关正文误判。"""
    assertion = "业务对象只能由所属 Account 访问；跨账户访问统一返回资源不存在或不可见语义。"
    assert supported(
        "业务对象只能由所属 Account 访问；跨账户访问统一返回资源不存在或不可见语义。", assertion
    )
    assert not supported("端点连续失败达到阈值后会被自动停用，需要人工恢复。", assertion)


def test_forbidden_conclusions_use_substring_matching() -> None:
    """禁止结论只认完整子串。

    它与正确结论共享同一批词，二元组重叠会把标准答案误判为违规（实测 17.3%）。
    代价是改写过的违规说法判不出来，属于漏判，方向上比误伤安全。
    """
    forbidden = "把 FP-4041 直接解释为 Payment ID 拼写错误"
    assert violated(f"我们认为{forbidden}，建议核对。", forbidden)
    assert not violated("FP-4041 同时表示不存在与不可见，不能据此判断拼写。", forbidden)


def row(identifier: str, *, multi: bool, passed: bool, unverified: int = 0) -> Row:
    """构造一条最小结果行；总览只读 multi_turn、task_pass 与 evidence_pass。"""
    return {
        "question_id": identifier,
        "question_type": "simple_retrieval",
        "multi_turn": multi,
        "doc_hit": 0,
        "doc_total": 1,
        "record_hit": 0,
        "record_total": 0,
        "assertion_hit": 0,
        "assertion_total": 1,
        "negative_hit": 0,
        "negative_total": 1,
        "evidence_pass": False,
        "task_pass": passed,
        "seconds": 0.0,
        "deviations": [],
        "signals": [],
        "unverified": unverified,
    }


def test_overview_separates_single_turn_from_multi_turn() -> None:
    """单轮与多轮必须分开统计。

    多轮题全部要求实例诊断，奖励的能力与单轮文档题不同；合并计分会把"某阶段擅长
    哪类题"这个唯一有意义的结论抹平——实测里单轮阶段的强项在单轮、manual-loop
    在多轮，合计分数却看不出差别。
    """
    rows = [
        row("a", multi=False, passed=True),
        row("b", multi=False, passed=False),
        row("c", multi=True, passed=True),
        row("d", multi=True, passed=True),
    ]
    table = "\n".join(overview({"manual-loop": rows}))
    assert "| manual-loop | 1/2 | 2/2 | 3/4 | 0/4 |" in table


def test_render_reports_unverified_counts() -> None:
    """报表列出未证实口径的出现次数与题清单（specs/009 SC-5 数据源）。

    既有指标口径不变：未证实统计是追加行，只在有命中时出现。
    """
    rows = [row("a", multi=False, passed=True, unverified=2), row("b", multi=True, passed=False)]
    table = "\n".join(run_module.render(rows, "multi-agent"))
    assert "未证实口径：2 处（1 题：a）" in table
    no_hit = "\n".join(run_module.render([row("c", multi=False, passed=True)], "multi-agent"))
    assert "未证实口径" not in no_hit


# --- play 的信号记录（FR-10）：单轮等待与多轮最终轮都不能漏进 signals ---


class Noop:
    """可重复 await 的立即完成对象，替代 manager.tasks[session]。"""

    def __await__(self) -> Iterator[object]:
        return iter(())


class FakeManager:
    """最小会话管理替身：按剧本逐轮返回状态与回答。"""

    def __init__(self, turns: list[tuple[str, str]], events: list[Event]) -> None:
        self.turns: list[tuple[str, str]] = list(turns)
        self.events: list[Event] = events
        self.tasks: dict[str, Noop] = {"session_0": Noop()}

    async def create_session(self, user: object) -> dict[str, str]:
        """固定返回同一个会话号。"""
        del user
        return {"id": "session_0"}

    async def submit_message(self, session: str, key: str, message: str, user: object) -> None:
        """不产生任何副作用。"""
        del session, key, message, user
        return None

    async def get_session(self, session: str, user: object) -> dict[str, object]:
        """按提交顺序弹出下一轮的状态与回答。"""
        del session, user
        status, answer = self.turns.pop(0)
        return {"status": status, "messages": [{"content": answer}]}


class FakeUser:
    """模拟用户替身：每次都披露一个事实，避免意外偏离。"""

    def __init__(self, conversation: dict[str, object]) -> None:
        del conversation
        return None

    def answer(self, question: str) -> tuple[str, list[str]]:
        """固定回复一条环境披露。"""
        del question
        return "环境是生产，账户是 acct_1", ["fact_1"]


def play_case(multi: bool, turns: int = 0) -> dict[str, object]:
    """构造一道最小题；multi 时给定澄清轮数预算。"""
    talk: dict[str, object] = {
        "mode": "progressive_disclosure" if multi else "single_turn_complete",
        "initial_user_message": "后台里能看到一笔支付但接口查不到",
    }
    if multi:
        talk["max_clarification_turns"] = turns
    return {
        "question_id": "Q-1",
        "question": "后台里能看到一笔支付但接口查不到",
        "conversation": talk,
    }


async def play_turns(
    monkeypatch: pytest.MonkeyPatch, turns: list[tuple[str, str]], multi: bool, budget: int
) -> conversation.Transcript:
    """用替身跑一道题，返回过程记录；每轮事件各声明一个不同类别的缺项。"""
    monkeypatch.setattr(run_module, "SimulatedUser", FakeUser)
    fields = ["account_id", "payment_id", "goal"]
    scripts: list[list[Event]] = []
    asked = 0
    for index, (status, _answer) in enumerate(turns):
        run = f"run_{index}"
        script_turn: list[Event] = [event("run.started", {}, run)]
        if status == "waiting_user":
            script_turn.append(ask_event([fields[asked]], run=run))
            asked += 1
        scripts.append(script_turn)
    submitted = 0

    def read(_session: str) -> list[Event]:
        """每次提交返回该轮自己的事件，模拟按会话读事件库。"""
        nonlocal submitted
        result: list[Event] = scripts[submitted] if submitted < len(scripts) else []
        submitted += 1
        return result

    manager = FakeManager(turns, [])
    _, _, script = await run_module.play(
        cast("SessionManager", cast("object", manager)), play_case(multi, budget), read
    )
    return script


async def test_single_turn_waiting_records_a_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """单轮题以 waiting_user 结束也要记一次 Exchange，不能因提前 break 漏记。"""
    script = await play_turns(
        monkeypatch, [("waiting_user", "请提供账户编号？")], multi=False, budget=0
    )
    assert [exchange.signal for exchange in script.exchanges] == ["waiting_user"]
    assert script.exchanges[0].categories == ["account_context"]
    assert script.exchanges[0].disclosed == []
    assert script.deviations == []


async def test_multi_turn_final_waiting_records_signal_and_deviation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """预算耗尽的最终轮先补记 Exchange 再记偏离，signals 才能覆盖最终轮。"""
    script = await play_turns(
        monkeypatch,
        [("waiting_user", "请提供账户编号？"), ("waiting_user", "还要环境信息？")],
        multi=True,
        budget=1,
    )
    assert [exchange.signal for exchange in script.exchanges] == [
        "waiting_user",
        "waiting_user",
    ]
    assert script.deviations == ["max_clarifications_exceeded"]


async def test_multi_turn_signals_count_each_waiting_round_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """正常走完的题每轮等待只记一次信号，最后作答不再追加。"""
    script = await play_turns(
        monkeypatch,
        [
            ("waiting_user", "请提供账户编号？"),
            ("waiting_user", "还要环境信息？"),
            ("completed", "结论是规则如下。"),
        ],
        multi=True,
        budget=2,
    )
    assert [exchange.signal for exchange in script.exchanges] == [
        "waiting_user",
        "waiting_user",
    ]
    assert script.deviations == []
