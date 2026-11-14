# Quickstart: 003-context-management 验证指南

> 状态：P0 批真机校准已回填（2026-11-15 冒烟三组实测，真机模型 deepseek-v4-flash）。

## 前置条件

```bash
# 容器内跑全量测试（权威口径；Windows 宿主机 fcntl 限制）
make up            # 或按 002 既有容器惯例
make test

# 真机冒烟需要模型 API 可用；compose 起栈后带治理键起 backend：
export FLOWPAY_DATASET_ROOT=<数据集绝对路径>   # shell 覆盖优先于 .env（仓库迁移后 .env 旧路径需手动修正）
docker compose run -d --no-deps --service-ports --name smoke-backend-003 \
  -e FLOWPAY_WORKFLOW=manual-loop \
  -e FLOWPAY_CONTEXT_CHAR_BUDGET=30000 \
  -e FLOWPAY_CONTEXT_TOOL_RESULT_BUDGET=1600 \
  -e FLOWPAY_CONTEXT_COMPACTION_TRIGGER_CHARS=30000 \
  -e FLOWPAY_CONTEXT_SUMMARY_CHAR_BUDGET=4000 \
  backend
docker exec smoke-backend-003 python scripts/smoke_003_context.py --timeout 900
```

## 场景 1：默认行为完全不变（US3 / SC-1）

```bash
cd backend && uv run pytest
```

实测（2026-11-15）：容器全量 `13 failed / 209 passed / 21 skipped`——13 条失败与 002 收官基线逐条一致（manual-loop 11 条数据源策略未配置 + 数据集相关 2 条），净增 22 条新测试全绿，零断言修改。

## 场景 2：压缩开启的六轮长会话（US1 / SC-2、SC-5、SC-8）

实测三组对照（manual-loop，六轮真机对话）：

| 触发线 | 压缩次数 | checkpoint 体积曲线 | 结论 |
|---|---|---|---|
| 30000（推荐试点） | 0 | 1159 → 17359 字符 | 002 投影（单结果钳 1600）+ 查询去重已把正常会话压在触发线内，压缩不触发——**触发线语义正确：原生流超过投影预算（投影即将整组删证据）才值得花一次摘要调用** |
| 8000（机制验证值） | 11-13 次/会话 | 轮内高频触发；一次真失控压缩 50762 → 17961 字符（-65%） | 压缩路径全线工作：0 失败、信封编号代码提取、跨压缩链累积（4 → 204 → 209 ids 单调递增）、disputed 零误拦且精准拦截一次编造引用 |
| multi-agent（同 30000） | 0 | 无消息流检查点（worker remember=False） | 压缩按设计仅对 manual-loop 生效；multi-agent 体积由投影 + 直连护栏 + P1/P2 治理 |

- **校准结论：`FLOWPAY_CONTEXT_COMPACTION_TRIGGER_CHARS` 试点推荐 30000**（与投影 char_budget 同值）；8000 仅为机制验证值，轮内高频触发会白付摘要调用（turn 5 output 10k tokens 大半是摘要）。
- 回答质量：六轮全部正常完成，第 5/6 轮正确延续历史结论（「之前会话中该支付存在的结论依然有效」），跨轮引用零误拦。

## 场景 3：直连节点护栏（US2 / SC-4）

容器用例全绿（policy=None 逐字一致 + 开启后截断带标记）。**multi-agent 实测遗留校准项**：直连护栏是「单变量段 ≤ char_budget」口径，synthesize 实测单请求 42-47k 字符仍超 30000 整体预算（question + 满额简报 + 报告合计）——**P1 应把直连变量段预算改为「char_budget − 其余部分」的剩余分配**，消除该口径差距。

## 场景 4：P1 失败证据与 P2 治理（SC-6、SC-7）

- 多任务并行 + 人为故障注入：故障调查者产出错误报告（error_type/message/任务目标），synthesize 照常给出部分结论
- findings 超 20 条：最旧合并为一条，`discard_stale` 对 merged 条目命中（改口作废仍生效）
- evidence 超上限：恢复时淘汰最旧，当轮 evidence（前端展示）完整

## 校准数据（P0 已回填）

| 项 | 试点值 | 依据 |
|---|---|---|
| compaction_trigger_chars | **30000** | 三组对照：30000 正常会话零触发（语义正确）；8000 高频触发白付成本；multi-agent 失控轮 50762 字符证明 30000 量级真实可达 |
| summary_char_budget | 4000 | 摘要正文实测均 < 4000，截断兜底未触发 |
| 直连变量段预算 | **P1 已改剩余分配并实测生效** | 修正前 multi-agent 冒烟 max request 43664 字符；修正后同场景 13957（-68%） |
| findings 封顶 | **P2 已实测生效** | multi-agent 六轮冒烟 findings 14 → 20（恰封顶）→ 20，改口作废对 merged 条目语义保持；evidence 60 ≤ 40000 空转 |

## 质量门禁

```bash
make quality      # ruff + basedpyright + pytest + 前端构建，全绿
```
