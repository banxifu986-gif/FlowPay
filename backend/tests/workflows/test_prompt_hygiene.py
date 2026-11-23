"""提示词卫生（CLAUDE.md「提示词卫生」条目）：发给模型的文本不携带内部实现词汇。

覆盖全部静态模型面向文本：prompt 常量、工具描述与 schema 内嵌说明（含服务端
绑定时的动态拼接）、协议纠正/收尾/编造核对消息。规范溯源（specs/XXX、FR-N、
任务号等）只允许出现在注释与 docstring 里；运行时拼装的报告/占位正文按可证伪
形态在 workflow 测试断言（test_multi_agent 的 error_report 用例），本文件不重复。
"""

import re
from typing import cast

import pytest
from openai.types.chat import ChatCompletionToolParam

from flowpay.core.agent.turn import CLOSING, DISPUTE
from flowpay.core.chat.compaction import SUMMARY_TEMPLATE
from flowpay.core.chat.projection import CLIP_MARKER
from flowpay.core.data_sources.tools import (
    LogToolSpec,
    SqlToolSpec,
    description_for,
)
from flowpay.core.json import JsonObject
from flowpay.core.knowledge.tools import (
    DocumentReadArguments,
    DocumentReadResult,
    DocumentSearchArguments,
    DocumentSearchResult,
    document_read_tool,
    document_search_tool,
)
from flowpay.core.verify.extractor import EXTRACT_SYSTEM_PROMPT
from flowpay.core.verify.verifier import VERIFY_SYSTEM_PROMPT
from flowpay.workflows.manual_loop import (
    LOG_TOOLS,
    SQL_TOOLS,
    SYSTEM,
    AskUserArguments,
    AskUserResult,
    ask_user_tool,
)
from flowpay.workflows.multi_agent.clarification import (
    ClarifyOutcome,
    clarify_tool,
)
from flowpay.workflows.multi_agent.intent import CLASSIFY_PROMPT, SET_INTENT
from flowpay.workflows.multi_agent.investigation import (
    DOC_WORKER_PROMPT,
    OUTPUT_RETRY_PROMPT,
    SET_RESPONSE,
    SET_TASKS,
    SQL_WORKER_PROMPT,
    SUPERVISOR_PROMPT,
    SYNTHESIZE_PROMPT,
)
from flowpay.workflows.multi_agent.simple import CHITCHAT_PROMPT, DOC_QA_PROMPT

# 内部实现词汇：规范目录与条款号、任务号、工作树代称、审查/实测过程词汇。
# 命中即说明内部编号进了模型可见文本（2026-11-23 只读审计清偿的形态）。
INTERNAL_VOCABULARY = re.compile(
    r"specs/|FR-\d|US\d|SC-\d|T0\d|R96|树A|树B|审查|e2e|grill|clarify 会话"
)

PROMPT_CONSTANTS: dict[str, str] = {
    "SUPERVISOR_PROMPT": SUPERVISOR_PROMPT,
    "SQL_WORKER_PROMPT": SQL_WORKER_PROMPT,
    "DOC_WORKER_PROMPT": DOC_WORKER_PROMPT,
    "SYNTHESIZE_PROMPT": SYNTHESIZE_PROMPT,
    "OUTPUT_RETRY_PROMPT": OUTPUT_RETRY_PROMPT,
    "CHITCHAT_PROMPT": CHITCHAT_PROMPT,
    "DOC_QA_PROMPT": DOC_QA_PROMPT,
    "CLASSIFY_PROMPT": CLASSIFY_PROMPT,
    "SYSTEM": SYSTEM,
    "EXTRACT_SYSTEM_PROMPT": EXTRACT_SYSTEM_PROMPT,
    "VERIFY_SYSTEM_PROMPT": VERIFY_SYSTEM_PROMPT,
    "SUMMARY_TEMPLATE": SUMMARY_TEMPLATE,
    "CLIP_MARKER": CLIP_MARKER,
    "CLOSING": CLOSING,
    "DISPUTE": DISPUTE,
}


def _param_description(param: ChatCompletionToolParam) -> str:
    """读取工具参数对象里的 description；TypedDict 嵌套取值需先宽化再窄化。"""
    function = cast("object", param["function"])
    return str(cast("dict[str, object]", function)["description"])


def _tool_descriptions() -> dict[str, str]:
    """全部发给模型的工具描述：静态装配面逐一取 description，含绑定拼接形态。

    工具描述来自 manual_loop / knowledge / data_sources / clarification 的装配
    面；SQL 与日志工具只测 spec 描述加 BOUND_HINT 拼接（构造 Tool 需要 query
    处理函数，而描述不经过它，无需真实执行链路）。
    """
    sql_spec = SQL_TOOLS[0]
    assert isinstance(sql_spec, SqlToolSpec)
    log_spec = LOG_TOOLS[0]
    assert isinstance(log_spec, LogToolSpec)
    descriptions = {
        "set_tasks": _param_description(SET_TASKS),
        "set_response": _param_description(SET_RESPONSE),
        "set_intent": _param_description(SET_INTENT),
        "ask_user(manual)": ask_user_tool(_manual_ask_handler).description,
        "ask_user(multi)": clarify_tool(_clarify_handler).description,
        "search_documents": document_search_tool(_search_handler).description,
        "read_document": document_read_tool(_read_handler).description,
        "query bound": description_for(sql_spec.description, {"account_id": "acct_demo"}),
        "log bound": description_for(log_spec.description, {"account_id": "acct_demo"}),
    }
    return descriptions


async def _manual_ask_handler(_arguments: AskUserArguments) -> AskUserResult:
    """工具描述只需构造协议对象即可读取；处理函数在本测试从不执行。"""
    return AskUserResult(missing=[], question="")


def _clarify_handler(_arguments: JsonObject) -> ClarifyOutcome:
    """追问通道的描述读取同上：只构造协议对象，不执行真实处理。"""
    return ClarifyOutcome()


async def _search_handler(_arguments: DocumentSearchArguments) -> DocumentSearchResult:
    """检索工具描述读取用空处理函数。"""
    return DocumentSearchResult(query="", passages=[], notice=None)


async def _read_handler(_arguments: DocumentReadArguments) -> DocumentReadResult:
    """读回工具描述读取用空处理函数。"""
    return DocumentReadResult(doc_id="", passages=[])


@pytest.mark.parametrize("name", sorted(PROMPT_CONSTANTS))
def test_prompt_constants_carry_no_internal_vocabulary(name: str) -> None:
    """prompt 常量不携带内部规范编号与过程词汇（CLAUDE.md 提示词卫生）。"""
    assert not INTERNAL_VOCABULARY.search(PROMPT_CONSTANTS[name]), name


@pytest.mark.parametrize("name", sorted(_tool_descriptions()))
def test_tool_descriptions_carry_no_internal_vocabulary(name: str) -> None:
    """工具描述（含服务端绑定时的动态拼接形态）不携带内部规范编号。"""
    assert not INTERNAL_VOCABULARY.search(_tool_descriptions()[name]), name
