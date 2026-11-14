"""恢复检查点后 seen 的重建口径（T019/R-5）：历史工具返回计入，assistant 文本不计。"""

import json
from typing import cast

from flowpay.core.agent import Budget
from flowpay.core.agent.trace import Tracer
from flowpay.core.agent.turn import AgentTurn
from flowpay.core.execution.contracts import TurnInput
from flowpay.core.execution.runtime import AgentRuntime

from .fixtures import Model


class NoopTracer:
    """只吞事件的追踪替身；本测试不发起任何模型或工具调用。"""

    async def emit(self, *_args: object, **_kwargs: object) -> None:
        return None


def restored_seen() -> set[str]:
    """从含历史轮的检查点恢复一轮，返回重建的 seen 集合。"""
    messages = [
        {"role": "system", "content": "你是排查助手。"},
        {"role": "user", "content": "排查 req_bm_030_01"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "query_sql", "arguments": "{}"},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "c1",
            "content": json.dumps({"rows": [{"id": "pay_bg_001_0003"}]}),
        },
        {"role": "assistant", "content": "上轮报告提到 pay_bg_001_9999"},
    ]
    turn = TurnInput(
        "继续看看这笔", [], {"messages": messages, "evidence": [], "collection_id": "fixture"}
    )
    runtime = AgentRuntime(cast("Tracer", cast(object, NoopTracer())), collection_id="fixture")
    agent = AgentTurn.start(turn, runtime, "系统提示词", Model(), Budget(4, 4))
    return agent.seen


def test_restored_seen_covers_user_and_tool_history() -> None:
    """用户给出的编号与历史轮工具真实返回的编号都在 seen 里（修复前后者缺失）。"""
    seen = restored_seen()
    assert "req_bm_030_01" in seen
    assert "pay_bg_001_0003" in seen


def test_assistant_history_text_does_not_enter_seen() -> None:
    """assistant 历史回答里的编号不计入——那是模型自己说的，不是工具返回的。"""
    assert "pay_bg_001_9999" not in restored_seen()


def test_findings_digest_ids_enter_shared_seen() -> None:
    """multi-agent 跨轮口径（审核回流）：上轮结论摘要里的编号并入 shared.seen。"""
    from flowpay.workflows.multi_agent.graph import fold_finding_record_ids
    from flowpay.workflows.multi_agent.state import Finding, SharedTurn

    shared = SharedTurn()
    shared.findings.append(
        Finding("query_sql", "source=pay_db acct_bg_001", 2, "2 行；首行 id=pay_bg_001_0003")
    )
    fold_finding_record_ids(shared)
    assert "pay_bg_001_0003" in shared.seen
