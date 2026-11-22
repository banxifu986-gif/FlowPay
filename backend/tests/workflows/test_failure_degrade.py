"""specs/009 US1：分级失效处置与降级作答的受控回归。

断言外部可见行为（事件序列、报告正文、轮次终态），不锁内部调用次数；注入
确定性错误必须验证新行为——tool.outage 载荷 failure_class=deterministic、
后续调用不再发起请求、无 run.retry——防止用既有 error_report 路径"空跑通过"
（审查 C1 可证伪形态）。
"""

from flowpay.core.json import JsonObject, to_json_object
from flowpay.workflows.multi_agent.state import (
    SharedTurn,
    SubtaskProgress,
    merge_task_progress,
    task_key,
)


def test_shared_turn_has_outage_and_progress_fields() -> None:
    """SharedTurn 新增三类字段：禁用登记、子任务断点、单任务工具计数（run 内单例）。"""
    shared = SharedTurn()
    assert shared.tool_outages == set()
    assert shared.task_progress == {}
    assert shared.task_tool_calls == {}


def test_task_key_is_worker_plus_objective() -> None:
    """幂等键 = worker 类型 + 任务目标：同任务同键，跨 worker 不同键。"""
    assert task_key("doc_worker", "查明重试策略") == task_key("doc_worker", "查明重试策略")
    assert task_key("sql_worker", "查明重试策略") != task_key("doc_worker", "查明重试策略")


def test_subtask_progress_round_trips_through_json() -> None:
    """断点实体经 JSON 往返不失真：恢复按载荷重建，键与字段保持。"""
    progress = SubtaskProgress(
        key=task_key("doc_worker", "查明重试策略"),
        worker="doc_worker",
        objective="查明重试策略",
        done_keys=["search:重试策略", "read:RULE-1"],
        findings=["1 个片段；首片 doc:RULE-1#c001"],
        evidence_refs=["doc:RULE-1#c001"],
        finished=True,
    )
    payload: JsonObject = to_json_object(progress)
    assert SubtaskProgress.restore(payload) == progress


def test_restore_rejects_malformed_progress_payload() -> None:
    """恢复按容忍口径：结构不合法的载荷按 None 处理，不让坏事件打断续跑。"""
    assert SubtaskProgress.restore(None) is None
    assert SubtaskProgress.restore("not-an-object") is None
    assert SubtaskProgress.restore({"worker": "doc_worker"}) is None  # 缺关键键


def test_merge_task_progress_keeps_finished_over_unfinished() -> None:
    """并行落库顺序无保证：已收束条目不被未收束同名条目覆盖（双向验证）。"""
    key = task_key("doc_worker", "查明重试策略")
    partial = SubtaskProgress(key, "doc_worker", "查明重试策略", ["search:重试策略"], [], [], False)
    done = SubtaskProgress(
        key,
        "doc_worker",
        "查明重试策略",
        ["search:重试策略", "read:RULE-1"],
        ["1 个片段；首片 doc:RULE-1#c001"],
        ["doc:RULE-1#c001"],
        True,
    )
    forward = merge_task_progress([partial, done])
    assert forward[key].finished is True
    assert forward[key].done_keys == ["search:重试策略", "read:RULE-1"]
    backward = merge_task_progress([done, partial])
    assert backward[key].finished is True
    assert backward[key].done_keys == ["search:重试策略", "read:RULE-1"]


def test_merge_task_progress_separate_keys_and_repeat_finished() -> None:
    """不同键互不覆盖；同名已收束条目以后写者为准（重放事件按序归一）。"""
    sql_key = task_key("sql_worker", "查明扣款状态")
    doc_key = task_key("doc_worker", "查明重试策略")
    first = SubtaskProgress(doc_key, "doc_worker", "查明重试策略", ["search:a"], [], [], True)
    second = SubtaskProgress(
        doc_key, "doc_worker", "查明重试策略", ["search:a", "read:B"], [], [], True
    )
    sql = SubtaskProgress(
        sql_key, "sql_worker", "查明扣款状态", ["payments account=1"], [], [], True
    )
    merged = merge_task_progress([first, second, sql])
    assert set(merged) == {doc_key, sql_key}
    assert merged[doc_key].done_keys == ["search:a", "read:B"]
