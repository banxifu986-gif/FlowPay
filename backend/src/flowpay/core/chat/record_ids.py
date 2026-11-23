"""业务记录编号的识别；编造引用防线与上下文投影共用同一份模式。"""

from __future__ import annotations

import re

# 业务记录编号形如 pay_bg_001_0003、req_bm_030_01：小写前缀加下划线，且带数字。
# 用它区分"真的引用了一条记录"和"提到了 FP-4021 这类错误码或字段名"。
RECORD_ID = re.compile(r"\b[a-z]{2,6}_[A-Za-z0-9_]*\d[A-Za-z0-9_]*\b")

# 业务记录编号的强词形（specs/009 US4 能力校验用）：小写前缀后必须出现纯数字段
# （pay_bg_001_0003、req_bm_030_01、tv_0120）。RECORD_ID 会命中 api_v2 这类
# "字母段尾带数字"的词形——能力校验拿它判 sql 会把含该词形的文档任务误改派
# （2026-11-23 审查 M3），故收窄；引用校验仍用宽松的 RECORD_ID（宁可漏判不误伤）。
BUSINESS_RECORD_ID = re.compile(r"\b[a-z]{2,6}(?:_[A-Za-z0-9]+)*_\d")


def record_ids(text: str) -> list[str]:
    """文本里出现的记录编号；用 finditer 以保留字符串类型。"""
    return [match.group(0) for match in RECORD_ID.finditer(text)]
