"""逐断言校验：发布前对最终作答做证据支撑核查的通用能力（specs/010）。

公开数据结构与判定词汇；装配、抽取、批量校验与处置映射的实现在后续
任务（specs/010-assertion-verification/tasks.md Phase 2）落位。
"""

from .contracts import (
    ActionKind,
    Assertion,
    AssertionAction,
    AssertionAudit,
    AssertionVerdict,
    Conflict,
    Overreach,
    OverreachDimension,
    Verdict,
)

__all__ = [
    "ActionKind",
    "Assertion",
    "AssertionAction",
    "AssertionAudit",
    "AssertionVerdict",
    "Conflict",
    "Overreach",
    "OverreachDimension",
    "Verdict",
]
