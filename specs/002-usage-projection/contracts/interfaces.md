# Interface Contracts: 模型用量聚合与请求前上下文投影

Date: 2026-11-14。前端可见契约只有 `message.assistant` payload 的 `usage` 键；其余为模块接口与 JSON 边界。

## 1. TurnResult（既有契约，新增字段）

```python
# flowpay.core.execution.contracts
@dataclass
class TurnResult:
    text: str
    saved_state: JsonObject
    status: TurnResultStatus = "completed"
    evidence: list[Passage] = field(default_factory=list)
    usage: UsageSummary | None = None      # 新增；零模型调用轮为 None
```

- SessionManager 只负责把 `outcome.usage` 序列化进 `message.assistant` payload，提交原子性与其余逻辑零改动。

## 2. 事件边界（不新增事件类型）

```json
// message.assistant payload 新增 usage 键（可缺失）
{"content": "...", "evidence": [...], "usage": {"model_calls": 3, "input_tokens": 1234, "output_tokens": 567, "total_tokens": 1801}}
```

- session_view 的 ChatMessage 增加 `usage` 字段（缺键宽容，缺键记 None）；既有事件流重放不受影响。
- SSE 推送通道零改动（只推已持久化事件，usage 已在持久化 payload 内）。

## 3. core/chat/usage_view.py（新增）

```python
@dataclass(frozen=True)
class UsageEntry:
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None

@dataclass(frozen=True)
class UsageSummary:
    model_calls: int
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None

def from_completion(completion: ChatCompletion) -> UsageEntry:
    """提取单次用量；usage 缺失/None/空对象 → 全 None 字段。不抛异常。"""

def sum_entries(entries: Iterable[UsageEntry]) -> UsageSummary:
    """聚合；model_calls = len(entries)；任一 entry 某位缺失则该位 None。"""
```

- 公共导出经 `core.chat` 的 `__init__.py`。

## 4. core/chat/projection.py（新增）

```python
@dataclass(frozen=True)
class ContextPolicy:
    char_budget: int
    tool_result_budget: int

def project_messages(
    messages: list[ChatCompletionMessageParam], policy: ContextPolicy
) -> list[ChatCompletionMessageParam]: ...
```

契约（docstring 与测试双落点，对应 FR-4~FR-7）：

1. **全函数**：任何畸形输入（缺字段、配对破损、空列表）原样返回，绝不抛异常；
2. **删除单元** = assistant 消息 + 其尾随全部 tool 结果整组，绝不拆散 tool_call_id 配对；
3. **永不删除**：首条 system、任何 user 消息、最后一段 assistant 交换；
4. **占位替换**保留 role=tool 与原 tool_call_id，content 为含 target/行数/记录编号清单的占位摘要；
5. **预算是尽力目标**：不可删集合自身超预算时，不变量 2（永不删）优先——原样保留不可删消息、尽力压缩其余部分，此时预算未被满足不视为投影失败（T014 对应用例）。

- policy=None 由调用方短路（不进入投影）；`project_messages` 本身要求显式 policy。

## 5. AgentTurn 变更（core/agent/turn.py）

```python
@classmethod
def start(cls, turn, runtime, system, model, budget, remember=True,
          context_policy: ContextPolicy | None = None) -> AgentTurn: ...
```

- `think()`：请求组装改为 `[*projected, *correction, *closing]`——投影只作用于检查点原生消息，纠正草稿与收尾系统消息永不投影。
- `think()` 内每次 `chat.call` 返回后累计 UsageEntry（含被 ModelOutputError 拒绝的调用）。
- `accept()` / `pause()` 以 `sum_entries` 写 TurnResult.usage；`give_up()`（零调用）保持 None。
- `__post_init__` seen 重建：question + user 消息 + **历史 tool 消息**编号（修复缺口）；disputed() 判定规则不动。
- AgentTurn 已由 `core.agent` `__init__.py` 导出，导出面不变。

## 6. settings（投影 opt-in）

- 新增配置键：投影字符预算与单条工具结果预算；缺省 / 0 = None 策略 = 现行为（默认关闭）。
- manual_loop / multi_agent 从 settings 读入并透传 `AgentTurn.start`；确切键名在 tasks.md 定。

## 7. fixtures 增强（tests/workflows/fixtures.py）

```python
def completion(content=None, calls=None, finish=None,
               usage: JsonObject | None = None) -> ChatCompletion: ...

class Model:
    def __init__(self, chunked_content: int = 1, chunked_tool_args: bool = False) -> None: ...
```

- `usage=None` 缺省：不改变既有用例的响应形状。
- 分片开关缺省关闭：既有单 chunk 发射行为不变。

## 8. 边界约束（门禁）

- usage_view / projection 只依赖 stdlib、openai SDK 类型与 `flowpay.core.json`；不新增第三方依赖。
- 禁止 import workflows / router / bootstrap / scripts（test_boundaries 强制）。
- workflows 只从 core 公共导出取用（ContextPolicy / project_messages / UsageSummary）。
- JSON 序列化只经 `flowpay.core.json` helper。
