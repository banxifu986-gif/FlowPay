"""逐断言校验的公开数据结构（specs/010 契约先行）。

与 specs/010-assertion-verification/contracts/verdict-schema.json 同构；两树
（能力侧/消费侧）以此为准，不得各自扩展字段。纯类型零实现，能力装配见
plan.md 分层接线。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# 四档判定：支持 / 部分支持 / 矛盾 / 证据不足。
Verdict = Literal["supported", "partial", "contradicted", "insufficient"]

# 处置动作：放行 / 收紧替换 / 未证实降级。
ActionKind = Literal["keep", "replace", "downgrade"]

# 越界维度：判定表述超出原文支持的哪一类条件。
OverreachDimension = Literal["对象", "时间", "数字", "条件"]


# 部分支持判定必须附越界定位（spec FR-4）：原文支持什么、表述越界到什么；
# 缺失时消费侧视同证据不足，不收紧只降级。
@dataclass(frozen=True)
class Overreach:
    """一条越界定位。"""

    dimension: OverreachDimension
    note: str


# 矛盾详情（clarify 拍板）：降级"未能核实"，分歧不进回答，详情随事件与
# 回放报告携带——冲突双方出处与冲突点。
@dataclass(frozen=True)
class Conflict:
    """一条证据冲突。"""

    sources: tuple[str, ...]
    point: str


@dataclass(frozen=True)
class Assertion:
    """原子断言：文本 + 依据引用。

    refs 为片段引用（doc:KB-XXX#cNNN / KB-XXX#cNNN 形态），消费侧先做
    存在性校验（spec FR-3）：不存在于本轮证据集的引用强制判证据不足，不经
    语义校验。record_ids 用于装配记录摘录，不影响 ID 存在性判定。
    """

    text: str
    refs: tuple[str, ...] = ()
    record_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class AssertionVerdict:
    """一条断言的四档判定。

    partial 必须携带 overreach 与 revision（收紧表述由校验调用一次性给出，
    处置零新增模型调用）；contradicted 必须携带 conflict。
    """

    assertion: Assertion
    verdict: Verdict
    overreach: Overreach | None = None
    revision: str | None = None
    conflict: Conflict | None = None


@dataclass(frozen=True)
class AssertionAction:
    """一条处置动作。

    sentence 是被处置的原句全文（句级等值匹配锚点，防错位替换）；replace
    在消费侧还要过数字/编号子集校验，越界降级。
    """

    sentence: str
    action: ActionKind
    revision: str | None = None


@dataclass(frozen=True)
class AssertionAudit:
    """一次校验的产物：判定清单、处置动作、fail-open 标记与最终文本。

    fail_open=True 时（调用失败/解析失败/装配异常/预算耗尽）actions 为空、
    text 等于输入原文——校验器故障永不阻断回答、不产生 run.failed
    （spec FR-6）。verdicts 为空表示无断言放行；处置幂等：处置后文本再过
    校验器不二次改写。矛盾分歧详情随 verdicts 条目内 conflict 携带
    （D10：事件载荷内联，不另设汇总字段）。
    """

    verdicts: tuple[AssertionVerdict, ...] = ()
    actions: tuple[AssertionAction, ...] = ()
    fail_open: bool = False
    text: str = ""
