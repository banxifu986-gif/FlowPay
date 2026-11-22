"""结论句引用标记校验（specs/009 US3 FR-6）：synthesize 输出前的确定性解析。

只看标记存在性（research D4 拍板）：句子带记录编号或片段引用即放行，编号真伪
由 synthesize 既有编造核对机制（shared.seen）负责；无标记的断言句确定性降级为
统一未证实口径。纯本地解析、零模型调用；宽松判定——宁可漏判（放行无断言线索
词的句子）不误伤正常作答，误判边界以测试固化。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from flowpay.core.chat.record_ids import RECORD_ID

# 统一未证实口径（FR-7）：作答与报表统计共用同一份词面。
UNVERIFIED_MARK = "未能核实"

# 片段引用标记形如 [doc:KB-TS-012#c001]；与 DOC_WORKER_PROMPT 的示例一致。
PASSAGE_REF = re.compile(r"\[doc:[^\]]+\]")

# 断言线索词：句子里出现才进入降级判定。没有线索词的句子（操作指引、客套话）
# 直接放行——降级的对象是"宣布了某个事实"的句子，不是所有无标记句子。裸"已"
# 与"已经"不入选：客套话（"已根据报告回答"）同样含它们，宁可漏判不误伤。
ASSERTION_CUES = (
    "成功",
    "失败",
    "是",
    "为",
    "允许",
    "不允许",
    "收取",
    "到账",
    "可查",
    "返回",
)

# 豁免形态：这些句式自带不确定性或非断言语义，不降级。
UNVERIFIED_PHRASES = ("未能确认", "未能核实", "待确认", "无法确认", "没有查到")

# 疑问句尾缀；疑问不是断言。
QUESTION_TAILS = ("？", "?")

# 断言句的最短有效正文：去掉前缀后过短的不降级（引导语、称呼等）。
MIN_ASSERTION_LENGTH = 4

# Markdown 列表前缀；降级时原样保留在句前。
BULLET_PREFIX = re.compile(r"^(?:[-*+]\s+)")

# 引导语豁免：冒号结尾是"下面展开"的引导，不是断言本身。
LEAD_IN_TAILS = (":", "：")


@dataclass(frozen=True)
class CitationAudit:
    """一次校验的产物：降级后的全文与被降级的原句列表。

    text 供 resolve_response 直接采用（T017 接入）；downgraded 供事件载荷与
    报表口径观测（SC-5：无引用裸奔断言为 0 的数据源）。
    """

    text: str
    downgraded: tuple[str, ...]


def has_citation(sentence: str) -> bool:
    """句内是否带任一引用标记：记录编号或片段引用。"""
    return bool(RECORD_ID.search(sentence) or PASSAGE_REF.search(sentence))


def is_assertion(sentence: str) -> bool:
    """是否为应降级的断言句：有线索词、非疑问、非豁免、非引导语、长度够。"""
    if any(phrase in sentence for phrase in UNVERIFIED_PHRASES):
        return False
    if any(sentence.endswith(tail) for tail in QUESTION_TAILS):
        return False
    if sentence.endswith(LEAD_IN_TAILS):
        return False
    if not any(cue in sentence for cue in ASSERTION_CUES):
        return False
    return len(sentence) >= MIN_ASSERTION_LENGTH


def split_sentences(text: str) -> list[str]:
    """按中英文句号、感叹号切句；分隔符保留在句尾。

    标记不跨句（误判边界：相邻句的编号不能替本句作保），逐句独立判定。
    """
    return re.findall(r"[^。！!?]+[。！!?]?", text)


def downgrade(sentence: str) -> str | None:
    """无标记断言句的降级文本；放行返回 None。"""
    if has_citation(sentence) or not is_assertion(sentence):
        return None
    body = sentence
    bullet = BULLET_PREFIX.match(body)
    prefix = bullet.group(0) if bullet else ""
    if prefix:
        body = body[len(prefix) :]
    return f"{prefix}{UNVERIFIED_MARK}：{body}"


def check_citations(text: str) -> CitationAudit:
    """校验全文：无标记断言句逐句降级，行结构与空行原样保留。

    已含统一口径的句子天然豁免（幂等：重复校验同一文本不二次降级）。
    """
    if not text:
        return CitationAudit(text="", downgraded=())
    downgraded: list[str] = []
    lines: list[str] = []
    for line in text.splitlines(keepends=True):
        newline = line.endswith("\n")
        content = line[:-1] if newline else line
        if not content.strip() or set(content.strip()) <= {"-"}:
            lines.append(line)
            continue
        rebuilt: list[str] = []
        for sentence in split_sentences(content):
            replacement = downgrade(sentence)
            if replacement is None:
                rebuilt.append(sentence)
            else:
                downgraded.append(sentence)
                rebuilt.append(replacement)
        lines.append("".join(rebuilt) + ("\n" if newline else ""))
    return CitationAudit(text="".join(lines), downgraded=tuple(downgraded))
