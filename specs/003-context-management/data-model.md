# Data Model: 上下文管理

## 1. ContextPolicy（扩展，core/chat/projection.py）

既有字段不变，新增（全部缺省 0 = 现行为）：

| 字段 | 类型 | 缺省 | 批次 | 语义 |
|---|---|---|---|---|
| compaction_trigger_chars | int | 0 | P0 | >0 启用压缩；原生消息流字符总量超过该值时触发 |
| summary_char_budget | int | 4000 | P0 | 摘要正文字符硬上限（代码截断兜底） |
| report_char_budget | int | 4096 | P1 | worker 报告产出端字符上限（0=不截断）；P0 阶段读取端用模块常量，P1 迁入 |

P2 不进 ContextPolicy：findings 上限为模块常量 `FINDINGS_CAP=20`；evidence 淘汰上限为 `PassageSet.prune` 参数，调用点定值（试点冒烟后回填）。

settings 对应键（FLOWPAY_ 前缀覆盖，loader 既有机制）：`context_compaction_trigger_chars`、`context_summary_char_budget`、`context_report_char_budget`。

## 2. 会话摘要信封（session_summary envelope）

中段 system 消息，content 为 JSON 文本：

```json
{
  "kind": "session_summary",
  "text": "<排查域五段摘要正文>",
  "record_ids": ["pay_bg_001_0003", "..."],
  "rounds": 5
}
```

| 字段 | 来源 | 约束 |
|---|---|---|
| kind | 代码写入 | 固定 `session_summary`；判定函数据此识别 |
| text | 模型生成 | 超过 summary_char_budget 由代码截断（R-6）；五段结构：已排除假设/当前主假设/已确认事实/已知条件与约束/下一步排查项 |
| record_ids | 代码从被删轮次 tool/user 内容提取（R-3） | 排序去重；模型不填写、不增删 |
| rounds | 代码统计 | 被压缩的完整交换数 |

判定规则（`is_summary_message`）：role=system 且 content 可解析为 JSON 且 `kind=="session_summary"`；解析失败按普通 system 消息（保留原位，不参与替换）。

流内不变量：至多一条摘要；再次压缩时新摘要替换旧摘要位置（与压缩区间起点取更靠前）。

## 3. 消息流分区（压缩视角）

| 分区 | 压缩行为 |
|---|---|
| 首条 system | 永不动 |
| 既有摘要（中段 system） | 永不动；text 作为 `<previous-summary>` 进合并 prompt |
| 其他中段 system（briefing/DISPUTE） | **永不在压缩区间内**（可删组只含 assistant+tool，与投影同定义）；其信息不经摘要吸收——briefing 下轮由 Case 重建、DISPUTE 为单轮纠正 |
| user 消息 | 永不动 |
| assistant+尾随 tool 整组 | 可压缩单元（与投影可删组同定义）；最近 2 个完整交换 + 最后一段交换保留 |
| 压缩产出 | 一条摘要 system 消息插在压缩区间起点 |

## 4. 摘要与合并 prompt（模块常量）

- `SUMMARY_PROMPT`：输入为序列化对话（tool 结果截断至短预算防止「摘要请求比被摘要上下文还大」）+ `<previous-summary>`（二次压缩起）+ 字符上限声明；输出五段正文，规则：编号原样保留、不新增正文未出现的编号、保留全部既有事实与已排除假设、更新主假设与下一步
- `SUMMARY_INPUT_CAP`：防御性输入上限（超长只取最老部分进摘要；正常永不命中）

## 5. Finding 合并条目（P2，multi_agent/state.py）

```json
{"tool": "merged", "target": "<成员 target 以「；」拼接>", "rows": <成员 rows 求和>, "digest": "<成员 digest 逐条拼接截断>"}
```

- 触发：轮末组装检查点前 `len(findings) > FINDINGS_CAP(20)`
- 合并对象：最旧 `len - cap + 1` 条并一条，新清单长度恰为 cap
- `discard_stale` 子串匹配语义保持：merged.target 含全部成员 target，改口作废仍命中

## 6. PassageSet.prune（P2，core/knowledge/passages.py）

- 签名：`prune(max_passages: int, max_chars: int) -> int`（返回淘汰数）
- 语义：按登记序保最新；仅作用于跨轮携带（恢复后调用），当轮新增证据不受影响；前端 evidence 展示取当轮 `retrieved.all()`，不受淘汰影响
- 接线调用点两处：manual-loop 的 `AgentTurn.start`（remember=True 恢复时）；multi-agent 的 `graph.py run()`（从 saved_state.evidence 恢复 shared.retrieved 后）

## 7. 治理留痕事件

| 事件 | payload | 时点 |
|---|---|---|
| context.compacted | before_chars / after_chars / rounds | 压缩成功落盘后 |
| context.compaction_failed | reason（网络/输出不合规/空摘要/超限失败） | 压缩跳过时 |

不新增其他事件类型；摘要调用本身沿用既有 model.request/model.response（payload `output: "internal"`）。

## 8. checkpoint 结构（修订后）

```
saved_state = {
  "messages": [...压缩后消息流（含摘要）...],   # 修订：002 为未压缩原生消息
  "evidence": [...], "collection_id": ...,
  "model_calls": ..., "tool_calls": ...,       # budget 口径不变（压缩调用不占）
  <阶段自有字段：case/queried/findings/...>
}
```

向后兼容：旧检查点（无摘要）零迁移可用；新检查点（含摘要）在压缩关闭配置下恢复亦无害（普通 system 消息）。

## 9. 持久化边界（不变）

事件日志 append-only 语义不变；每轮 `run.finished.state` 是新快照，历史快照不可变；投影结果仍不持久化（每请求对当前消息流确定性重算）。
