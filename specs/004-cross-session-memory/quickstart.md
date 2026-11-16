# Quickstart: 004 真机校准指南

> 状态：真机校准已回填（2026-11-15 冒烟实测，模型 ark deepseek-v4-flash + ollama qwen3-embedding:0.6b CPU）。

## 启用

```bash
# .env 或环境变量（缺省全 0 = 关闭 = 现行为）
FLOWPAY_MEMORY_RECALL_CHAR_BUDGET=2000   # >0 启用记忆整链路；注入段落字符预算
FLOWPAY_MEMORY_SEMANTIC_LIMIT=2          # 语义兜底候选数（0=仅键匹配）
FLOWPAY_MEMORY_MAX_NOTES=200             # 总量封顶
# P2 硬化
FLOWPAY_CONTEXT_BRIEFING_CAP=6           # manual-loop briefing 消息数封顶（0=现行为）
```

## 冒烟（scripts/smoke_004_memory.py）

照 003 模式起带键 backend，容器内执行（见 spec.md 验收）：

```bash
docker compose run -d --no-deps --service-ports --name smoke-backend-004 \
  -e FLOWPAY_WORKFLOW=multi-agent \
  -e FLOWPAY_MEMORY_RECALL_CHAR_BUDGET=2000 \
  -e FLOWPAY_MEMORY_SEMANTIC_LIMIT=2 \
  backend
docker exec smoke-backend-004 python scripts/smoke_004_memory.py --timeout 900
```

四阶段契约（2026-11-15 实测全部通过）：

- **A 写入**：`memory.extracted` 落库，单轮排查结论实测 ~2.7k 字符 / 6-8 个结构化键；
- **B 键召回**：同编号新会话 `matched=key`，记忆话术出现在 plan/worker/synthesize **3/3** 请求；
- **C 语义兜底**：同症状无编号问法 `matched=semantic`，阈值 0.35 下命中 2 条并注入（5916 字符，受 recall_char_budget 裁剪）；
- **D reactive compact**：真机未诱导（需专门小预算长会话配置），各分支由 test_reactive_compact.py 单测覆盖。

## 校准数据（真机实测）

| 校准项 | 试点值 | 实测结论 |
|---|---|---|
| RECALL_SEMANTIC_THRESHOLD | 0.35 | 同症状不同编号问法命中、无噪声误召——维持 0.35 |
| memory_recall_char_budget | 2000 | B 阶段注入 3 条笔记 2758+ 字符被正常裁剪；语义 2 条 5916 字符裁剪生效 |
| memory_max_notes | 200 | 冒烟量级（≤10 条）远未触及；长期积累再评估 |
| 抽取嵌入耗时 | — | ollama CPU 实测：短查询 ~8s，长正文（~2k 字符）**21-35s**；抽取是 run.finished 后的收尾步骤，回答先到、`memory.extracted` 事件滞后数十秒属预期，冒烟脚本按收尾事件轮询 |
| semantic_limit | 2 | 键未命中时注入 ≤2 条语义候选，噪声可控 |

## 注意

- **写入时嵌入**：语义兜底要求笔记落库时生成向量（manager 收尾嵌入正文）；嵌入失败降级为无向量落库（键召回不受影响），发 `memory.extract_failed`（error_type 前缀 `embedding:`）。
- **冒烟竞态**：抽取收尾慢于 run.finished，验证脚本必须轮询 `memory.extracted/skipped/failed`（wait_for_memory_events 已实现），读完立即断言会偶发失败。
- 语义兜底不发生时（semantic_limit=0）零嵌入调用；键召回零成本（全表内存比对）。
