# Interface Contracts: LangGraph 多 Agent 编排

Date: 2026-11-12。本项目无对外 API 变更（HTTP/SSE 契约不动）；本文定义轮内新增的模块接口与 JSON 边界。

## 1. Workflow Protocol（既有，不变）

```python
# flowpay.core.execution.contracts
class Workflow(Protocol):
    id: str            # multi-agent: "flowpay-multi-agent-support"
    version: str       # "1"
    card: StageCard
    async def run(self, turn: TurnInput, runtime: AgentRuntime) -> TurnResult: ...
```

- `TurnInput`：user_message / chat_history / saved_state / resumed（不变）
- `TurnResult`：text / saved_state / status(`"completed"|"waiting_user"`) / evidence（不变）
- SessionManager 对接零改动。

## 2. 阶段注册（router.py）

```python
StageFactory = Callable[[ChatModel, Callable[[str], str]], Workflow]   # 签名不变
STAGES: dict[str, StageFactory] = {
    "manual-loop": ManualLoopWorkflow,   # 原 "s4" / S4Workflow
    "multi-agent": MultiAgentWorkflow,   # 新增；settings 默认切到此处
}
```

- workflow id 不变：manual-loop 仍是 `flowpay-multi-turn-rag`；旧会话 identity 校验行为不变（Conflict 即迁移提示）。
- `MultiAgentWorkflow(model, prepare_query)` 与 manual-loop 同构；额外接受 `max_concurrency: int | None = None`（测试用，透传 `ainvoke(config={"max_concurrency": ...})`）。

## 3. multi_agent 包公共接口（__init__.py 只导出 MultiAgentWorkflow）

```python
# state.py
class GraphState(TypedDict): ...            # 见 data-model.md
@dataclass class SharedTurn: ...
@dataclass(frozen=True) class InvestigationTask: kind: Literal["sql","docs"]; objective: str
@dataclass(frozen=True) class WorkerReport: worker: Literal["sql","docs"]; text: str
@dataclass(frozen=True) class Finding: tool: str; target: str; rows: int; digest: str

# intent.py
class IntentDecision(BaseModel): intent: Literal["troubleshoot","docs","chitchat"]; reason: str
def conditions_route(user_message: str) -> str | None   # 规则短路；命中返回意图，未命中 None
CLASSIFY_PROMPT: str                                    # 分类系统提示词（包内私有使用，非跨包契约）
SET_INTENT: ChatCompletionToolParam                     # set_intent 工具定义；Schema 由 IntentDecision.model_json_schema() 生成，additionalProperties=False
def read_intent(completion: ChatCompletion) -> IntentDecision | None  # 未调工具或参数不合法返回 None，调用方兜底

# state.py
NodeOutput = dict[str, object]
class Node(Protocol):                                   # langgraph 节点协议：参数可按关键字传入，Callable 别名匹配不上
    def __call__(self, state: GraphState) -> Awaitable[NodeOutput]: ...
def restore_findings(state: object) -> list[Finding]    # 恢复跨轮结论摘要；缺键宽容

# investigation.py
def claim(shared: SharedTurn, tool: str, target: str) -> str | None
    """纯同步临界区：check-then-act 之间不得 await；获准返回 None 并记账，拒绝返回说明文案。"""
def discard_stale(shared: SharedTurn, changed: dict[ConditionField, str]) -> None
    """改口换账户/环境时作废旧 scope 下的 findings 与 done 键（US4 改口语义）。"""

# graph.py（节点装配）
def dispatch(state: GraphState) -> list[Send] | str     # plan 之后的条件边：按 kind Send 分发；空任务返回 "synthesize"
```

节点函数（`async def (state: GraphState) -> dict`）为包内私有，不经 __init__ 导出。

## 4. set_intent 工具 Schema（模型可见）

```json
{
  "name": "set_intent",
  "description": "报告本轮用户消息的意图分类结果。",
  "parameters": {
    "type": "object",
    "additionalProperties": false,
    "properties": {
      "intent": {"type": "string", "enum": ["troubleshoot", "docs", "chitchat"]},
      "reason": {"type": "string", "minLength": 1, "maxLength": 200}
    },
    "required": ["intent", "reason"]
  }
}
```

由 `IntentDecision.model_json_schema()` 生成，与 `ask_user_tool` 同一装配手法。

## 5. saved_state JSON 边界

Schema 见 data-model.md。兼容性规则：

- `case` / `queried` / `evidence` 三键与 manual-loop 产出完全同构（MultiAgentWorkflow 既能恢复 manual-loop 写下的检查点，也能被相同键恢复——不承诺跨 workflow id 恢复，identity 校验在前）。
- `findings` / `stop_reason` 为新增键；恢复端对缺键宽容（按空处理），单条 finding 内部结构严格（与 `Case.restore` 同口径）。
- 序列化只用 `flowpay.core.json` helper；workflows 层不得直引 `json`/`os`（test_boundaries 强制）。

## 6. 边界约束（门禁）

- multi_agent 只 import `flowpay.core.*`、`flowpay.workflows.manual_loop`、langgraph。
- 禁止：`openai`、`httpx`、`psycopg`、`os`、`json` 直引（tests/core/test_boundaries.py）。
- core 依赖方向不变：core 不 import workflows/multi_agent。
