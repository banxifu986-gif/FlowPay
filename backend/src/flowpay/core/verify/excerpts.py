"""记录摘录池的登记与封顶（specs/010 FR-1 在线口径）。

在线链路的"断言中记录编号对应记录摘录"数据源：sql worker 业务查询的工具结果
原文按记录编号登记进共享摘录池，经 gate → auditor 传入校验器。放 core 的原因：
摘录是校验证据装配的输入口径（与 assembly.business_record_ids 同一套识别词形），
且 workflows 层不得自行做 JSON 序列化（基座边界测试）；回放侧的摘录恢复是
scripts/benchmark 的旁路口径，两者封顶口径保持一致。
"""

from __future__ import annotations

import json
from collections.abc import Sequence

from flowpay.core.json import require_object
from flowpay.core.verify.assembly import business_record_ids

# 摘录池的上限（模块常量不进配置面）：单条摘录字符封顶与条数、总字符双封顶；
# 任一超限都从最早登记的编号开始淘汰。
RECORD_EXCERPT_CAP = 2000
RECORD_EXCERPTS_MAX = 64
RECORD_EXCERPTS_CHAR_CAP = 32000


def register_record_excerpts(pool: dict[str, str], rows: Sequence[object]) -> None:
    """把业务查询结果各行按行内出现的记录编号登记进共享摘录池。

    摘录即该记录行的 JSON 原文（按记录行裁剪，单条截断到 RECORD_EXCERPT_CAP），
    行内每个业务编号各登记一份——断言无论绑定行自身的编号还是行内引用的编号，
    校验器都有对应摘录可查。同一编号重复登记覆盖为新值（幂等），覆盖不刷新
    登记位：淘汰始终从最早登记的编号开始。
    """
    for row in rows:
        text = json.dumps(require_object(row), ensure_ascii=False)
        # 摘录是判定的语境定位，不是原文回放：单条截断与回放侧同口径。
        excerpt = text[:RECORD_EXCERPT_CAP]
        for identifier in business_record_ids(text):
            pool[identifier] = excerpt
    cap_record_excerpts(pool)


def cap_record_excerpts(pool: dict[str, str]) -> None:
    """摘录池双封顶收敛：条数或总字符超限时从最早登记的编号开始淘汰。

    dict 保持插入序，覆盖已有键不改变位置，"最旧"语义稳定（PassageSet.prune
    同款保尾淘汰形态）；单条截断封顶小于总字符封顶，单条不会独自挤空整池。
    """
    total = sum(len(value) for value in pool.values())
    while pool and (len(pool) > RECORD_EXCERPTS_MAX or total > RECORD_EXCERPTS_CHAR_CAP):
        total -= len(pool.pop(next(iter(pool))))
