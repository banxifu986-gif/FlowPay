# Interface Contracts: 上下文管理

## 1. ContextPolicy（扩展，core/chat/projection.py）

```python
@dataclass(frozen=True)
class ContextPolicy:
    char_budget: int            # 既有：投影请求字符预算
    tool_result_budget: int     # 既有：单条旧工具结果预算
    compaction_trigger_chars: int = 0      # 新增：压缩触发线，>0 启用
    summary_char_budget: int = 4000        # 新增：摘要正文硬上限
    report_char_budget: int = 4096         # 新增（P1）：报告产出端上限
```

bootstrap 装配：投影两项 >0 维持既有构造；压缩两项独立读入（缺省 0）；P1 的 report 项随批次加入。workflows 只透传。

## 2. core/chat/compaction.py（新增，纯函数层）

```python
def is_summary_message(message: JsonObject | ChatCompletionMessageParam) -> bool
"""判定会话摘要信封；解析失败按普通 system 返回 False。宽松联合：两侧调用点免转换。"""

def select_compaction_range(
    messages: list[JsonObject], trigger_chars: int, keep_recent_rounds: int
) -> list[list[int]] | None
"""返回待压缩整组的下标分组（assistant+尾随 tool，与投影可删组同定义）；
不足（可压缩组数 <= keep_recent_rounds、总体积未超线）返回 None。全函数。"""

def compact_messages(
    messages: list[JsonObject], groups: list[list[int]],
    summary_text: str, summary_budget: int,
) -> tuple[list[ChatCompletionMessageParam], list[str], int]
"""用摘要信封替换被压缩组，返回 (新消息流, 提取的 record_ids, 压缩轮数)。产出与投影同款协议类型列表。
新摘要插回旧摘要位置或区间起点（取更靠前）；流内至多一条摘要。纯函数，不改输入。"""

def summary_user_prompt(
    messages: list[JsonObject], previous_summary: str | None, char_budget: int
) -> str
"""构造摘要请求的 user 正文：序列化对话（tool 结果截断至短预算）+
<previous-summary>（二次压缩起）+ 上限声明。纯函数。"""
```

约定：全部全函数（畸形输入原样返回/None），不抛异常；record_ids 提取复用 `core/chat/record_ids.py`；类型只依赖 stdlib/openai 类型/flowpay.core.json。

## 3. AgentTurn 变更（core/agent/turn.py）

```python
async def _compact_if_needed(self) -> None
"""think() 入口、投影之前调用。remember=False 或未配置触发线时直接返回。
触发→摘要调用（chat.call output="internal"，不 spend_model_calls，usage 入账）→
compact_messages 替换消息流 → trace context.compacted。任何失败：跳过 +
trace context.compaction_failed，消息流不动。"""

def known_record_ids(messages: MessageLog) -> list[str]   # 既有函数扩展
"""扫描 user/tool 消息 + is_summary_message 判真的 system 消息（只读信封
record_ids 结构化字段，不对摘要正文跑 record_ids()）。"""
```

不变量：压缩不拆 tool_call 配对；首条 system/任何 user/最后一段交换/既有摘要永不动；`MessageLog.restore` 零改动；checkpoint 的 messages 为压缩后消息流。

## 4. core/chat/projection.py（新增纯函数）

```python
def clip_text(text: str, budget: int, marker: str = CLIP_MARKER) -> str
"""超限尾部截断 + 追加显式标记（含截断字符数与「可按记录编号重查」提示）；
未超限原样返回。"""
```

直连节点格式模板留在调用点；节点代码形如 `f"{question}\n\n{briefing}"` 改为
`f"{question}\n\n{clip_text(briefing, budget) if policy else briefing}"`（或等价的局部封装），**core 不内置节点文案**。

## 5. 直连节点签名变更（workflows/multi_agent/）

| 节点 | 文件 | 变更 | 变量段 |
|---|---|---|---|
| route | graph.py | 闭包捕获 context_policy | 无（裸问题） |
| plan | investigation.py | 同上 | 已知条件与已执行查询简报 |
| synthesize | investigation.py | 同上 | 简报 + 每份报告（clip_text 上限 4096） |
| doc_qa | simple.py | 同上 | 检索片段 serialize |
| chitchat | simple.py | 同上 | 无 |

policy=None 时输出与现 f-string 拼接逐字一致（回归契约，测试钉住）。

## 6. P1/P2 接口（随批次落地）

```python
# P1 investigation.py
def error_report(worker, objective, error: Exception, budget: int) -> WorkerReport
def budget_report(shared, worker, baseline: int, budget: int) -> WorkerReport
# investigate() 产出前：report_text 经 clip_text(report_char_budget)（0=不截断）

# P2 state.py
def merge_old_findings(findings: list[Finding], cap: int) -> list[Finding]
# P2 passages.py
def prune(self, max_passages: int, max_chars: int) -> int
```

## 7. settings（core/config/settings.py）

| 键 | 类型 | 缺省 | 批次 |
|---|---|---|---|
| context_compaction_trigger_chars | int | 0 | P0 |
| context_summary_char_budget | int | 4000 | P0 |
| context_report_char_budget | int | 4096 | P1 |

缺省即现行为（US3 契约）；既有 `context_char_budget`/`context_tool_result_budget` 不动。

## 8. 事件边界

新增 `context.compacted` / `context.compaction_failed`（payload 见 data-model §7）；摘要调用复用 model.request/model.response（`output: "internal"`，client.py:35 既有）。不新增其他事件类型。

## 9. 边界约束（test_boundaries 门禁）

- core/chat 不 import workflows/router/bootstrap/scripts/sqlite3
- 压缩编排只经 `self.chat`（ChatClient）与 `self.runtime.tracer`（EventSink）
- 摘要信封 record_ids 提取只依赖 `core/chat/record_ids.py`
