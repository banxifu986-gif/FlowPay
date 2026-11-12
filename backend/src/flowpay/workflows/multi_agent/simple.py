"""轻分支节点：闲聊与文档问答，单节点产出回答后直达 END。

闲聊不经过 AgentTurn：没有工具与预算记账，直调模型一次（跨轮决策）。文档问答
做一次检索再作答，片段登记进共享 PassageSet，随 TurnResult.evidence 交给界面
做引用展开——引用编号协议层不改写正文（答案契约），由界面据实际片段标注。
"""

from __future__ import annotations

from collections.abc import Callable

from openai.types.chat import ChatCompletionMessageParam

from flowpay.core.execution.runtime import AgentRuntime
from flowpay.core.llm.chat import ChatModel
from flowpay.core.llm.chat.messages import answer_text

from .state import GraphState, Node, NodeOutput, SharedTurn

CHITCHAT_PROMPT = (
    "你是 FlowPay 技术支持助手。用户这条消息是日常对话，不是业务问题：简短友好地回应，"
    "可以顺带说明你能排查支付与 Webhook 问题、解答平台文档，不要展开业务内容。"
)

DOC_QA_PROMPT = (
    "你是 FlowPay 技术支持助手，回答用户关于平台规则的文档问题。"
    "只依据给出的知识库片段作答，在引用了片段内容的句子后标注片段编号"
    "（如 [doc:KB-TS-012#c001]）。片段不足以回答时，明确说明未能找到依据，不要编造引用。"
)

NO_EVIDENCE = "没有在知识库中找到能回答这个问题的依据。可以换个问法再问一次。"


def chitchat_node(model: ChatModel, runtime: AgentRuntime) -> Node:
    """闲聊：一次直调模型，不提供任何工具（SC-2），也不检索。"""

    async def node(state: GraphState) -> NodeOutput:
        messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": CHITCHAT_PROMPT},
            {"role": "user", "content": state["user_message"]},
        ]
        completion, _ = await runtime.chat(model).call(messages)
        return {"answer": answer_text(completion), "waiting": False}

    return node


def doc_qa_node(
    model: ChatModel,
    prepare_query: Callable[[str], str],
    runtime: AgentRuntime,
    shared: SharedTurn,
) -> Node:
    """文档问答：一次检索 + 一次作答；零命中直接明示依据不足，不消耗模型调用。"""

    async def node(state: GraphState) -> NodeOutput:
        query = prepare_query(state["user_message"])
        hits = shared.retrieved.add(await runtime.search_knowledge(query, limit=5))
        if not hits:
            return {"answer": NO_EVIDENCE, "waiting": False}
        messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": DOC_QA_PROMPT},
            {
                "role": "user",
                "content": (
                    f"{state['user_message']}\n\n"
                    f"以下是知识库检索到的片段，编号用于引用：\n{shared.retrieved.serialize()}"
                ),
            },
        ]
        completion, _ = await runtime.chat(model).call(messages)
        return {"answer": answer_text(completion), "waiting": False}

    return node
