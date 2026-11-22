"""US4 plan 校验：任务目标与 worker 能力不匹配时的改派/丢弃判定（函数级）。"""

from typing import Literal, cast

from flowpay.workflows.multi_agent.investigation import expected_kind, validate_tasks
from flowpay.workflows.multi_agent.state import InvestigationTask

Kind = Literal["sql", "docs"]


def task(kind: str, objective: str) -> InvestigationTask:
    """构造一个待校验的子任务。"""
    return InvestigationTask(cast(Kind, kind), objective)


def test_expected_kind_by_record_id() -> None:
    """带业务记录编号的目标属于业务记录域。"""
    assert expected_kind("查支付 pay_bg_001_0003 的状态") == "sql"


def test_expected_kind_by_business_terms() -> None:
    """业务记录关键词命中即判 sql；规则/文档关键词命中即判 docs。"""
    assert expected_kind("查明这笔支付有没有触发 Webhook 投递") == "sql"
    assert expected_kind("查平台文档里 Webhook 重试策略的规则") == "docs"


def test_expected_kind_none_when_both_or_neither() -> None:
    """关键词两类同时命中或都不命中时不质疑模型的 kind；记录编号是最强信号。"""
    assert expected_kind("查支付和事件的文档规则") is None
    assert expected_kind("查明这件事的来龙去脉") is None
    assert expected_kind("查支付 pay_bg_001_0003 的事件，并对照文档规则") == "sql"


def test_validate_tasks_reassigns_mismatched() -> None:
    """分发前校验：目标与 worker 不匹配的任务被改派到匹配的 worker。"""
    tasks = [task("docs", "查支付 pay_bg_001_0003 的状态")]
    assert validate_tasks(tasks) == ([task("sql", "查支付 pay_bg_001_0003 的状态")], [])


def test_validate_tasks_final_drops_mismatched() -> None:
    """重计划复核阶段（final=True）：仍不匹配的任务丢弃，其余保留。"""
    tasks = [
        task("docs", "查平台文档里重试策略的规则"),
        task("docs", "查支付 pay_bg_001_0003 的状态"),
    ]
    reassigned, dropped = validate_tasks(tasks, final=True)
    assert reassigned == [task("docs", "查平台文档里重试策略的规则")]
    assert dropped == [task("docs", "查支付 pay_bg_001_0003 的状态")]


def test_validate_tasks_keeps_ambiguous_objective() -> None:
    """两类关键词都不命中的目标维持模型给的 kind，不进丢弃清单。"""
    tasks = [task("docs", "查明这件事的来龙去脉")]
    assert validate_tasks(tasks) == (tasks, [])
