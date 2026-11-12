"""意图识别：规则短路优先，LLM 结构化分类兜底。

兜底意图是 troubleshoot：排查分支包含文档工具，是 docs 的超集，路由错误的
代价最低。业务编号是强信号——用户报出编号基本就是在排查，规则直接放行，
省掉一次分类调用（SC-1）。
"""

from typing import ClassVar, Literal

from openai.types.chat import ChatCompletion, ChatCompletionToolParam
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from flowpay.workflows.manual_loop import conditions_in

DEFAULT_INTENT = "troubleshoot"

CLASSIFY_PROMPT = (
    "你是 FlowPay 技术支持助手的意图分类器。按用户消息判断意图，调用 set_intent 报告结果：\n"
    "- troubleshoot：排查支付、退款、Webhook、事件通知等业务问题；\n"
    "- docs：询问平台规则、使用方法等知识性问题；\n"
    "- chitchat：问候、感谢、闲聊等与业务无关的对话。\n"
    "判断不了时选 troubleshoot。"
)


class IntentDecision(BaseModel):
    """分类结果：意图类别加判断理由；理由仅供追踪，不参与路由。"""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")
    intent: Literal["troubleshoot", "docs", "chitchat"]
    reason: str = Field(min_length=1, max_length=200, pattern=r"\S")


# 与 ask_user_tool 同一装配手法：参数 Schema 由 pydantic 模型生成，extra 约束需要显式写出。
SET_INTENT: ChatCompletionToolParam = {
    "type": "function",
    "function": {
        "name": "set_intent",
        "description": "报告本轮用户消息的意图分类结果。",
        "parameters": {**IntentDecision.model_json_schema(), "additionalProperties": False},
    },
}


def read_intent(completion: ChatCompletion) -> IntentDecision | None:
    """从模型响应读取 set_intent 调用并解析；没有调用或参数不合法返回 None，交给调用方兜底。"""
    calls = completion.choices[0].message.tool_calls
    if not calls or calls[0].type != "function" or calls[0].function.name != "set_intent":
        return None
    try:
        return IntentDecision.model_validate_json(calls[0].function.arguments)
    except ValidationError:
        return None


def conditions_route(user_message: str) -> str | None:
    """规则短路：消息里带业务编号时直接判为排查；未命中返回 None 交给 LLM。"""
    return DEFAULT_INTENT if conditions_in(user_message) else None
