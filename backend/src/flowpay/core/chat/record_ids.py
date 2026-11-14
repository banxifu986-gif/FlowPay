"""业务记录编号的识别；编造引用防线与上下文投影共用同一份模式。"""

from __future__ import annotations

import re

# 业务记录编号形如 pay_bg_001_0003、req_bm_030_01：小写前缀加下划线，且带数字。
# 用它区分"真的引用了一条记录"和"提到了 FP-4021 这类错误码或字段名"。
RECORD_ID = re.compile(r"\b[a-z]{2,6}_[A-Za-z0-9_]*\d[A-Za-z0-9_]*\b")


def record_ids(text: str) -> list[str]:
    """文本里出现的记录编号；用 finditer 以保留字符串类型。"""
    return [match.group(0) for match in RECORD_ID.finditer(text)]
