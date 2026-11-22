# 快速开始：009 harness 鲁棒性验证

前置：主仓 Docker 全栈可用（`make up`）、R96B 基线数据可读（`var/benchmark/20261122-101317-2ca71c`）、`.env` 已配模型与嵌入密钥。实施在新分支进行（spec 阶段只读）。

## 1. 单元与集成测试（B1–B5 门禁）

```bash
make quality          # ruff + basedpyright + pytest 全绿；失败基线为空
# 或容器内
docker compose exec backend pytest
```

关键用例清单（对应 SC）：

- 确定性错误可证伪验证（SC-2）：注入嵌入链路 400（结构性拒绝），断言 `tool.outage` 载荷 `failure_class=deterministic`、后续对该链路调用不再发起请求、事件序列无 `run.retry`、任务按 error_report 收束。
- 工具链路禁用：同一 run 内禁用后，后续调用立即失败终止对应任务，不再发起请求。
- 降级作答（SC-3）：部分子任务无证据时整轮正常完成，作答含"未能核实"且无 `run.failed`；synthesize 空输出注入 → `model.retry` 恰一次 → 仍空则占位完成、无 `run.failed`（FR-12）。
- 节点级续跑：中断恢复后已完成子任务零重查——恢复后的查询键集合与断点前不交集（SC-4）。
- 配额豁免：恢复轮日回合计量为 0（`daily_run_count` 过滤 resumed=true）；并发名额恢复前后守恒。
- 引用校验：无标记断言句被降级/重写；带标记句子原样放行（SC-5）。
- plan 校验三路径：改派 / 重计划一次后成功 / 重计划后丢弃（事件可查）。
- 预算收束：达到 `investigation_tool_budget` 即收束并携带发现返回（SC-5 口径）。
- 多跳两段式：第二跳查询文本含第一跳结论值；单跳任务行为不变（SC-7 机制面）。

## 2. 冒烟（B6 第一步）

```bash
# R96B 同口径：gpt-6-luna + responses + medium + bge-m3 集合
make up
# 跑 --per-type 1（6 题），沿用 008 冒烟纪律：run.failed=0、事件链路正常、
# 实测 token/耗时外推 96 题；实际超外推 2 倍停手。
```

## 3. 96 题全臂（SC-1~SC-8 一次出数）

```bash
# 同 R96B 配置跑全量 96 题（约 2.5h）；产物目录记入交付报告。
# 报表核对：
#   SC-1 机制门：整题覆盖 ≥90%、重复问 0、等待轮带结论 100%（clarification.py 采纳轮口径）
#   SC-2 run.failed=0 + 确定性错误无重试事件
#   SC-5 无引用裸奔断言（报表未证实口径统计）
#   SC-6 证据全中 ≥50/96（R96B 54/96 − 噪声 4）
#   SC-7 multi_hop 证据全中 ≥11/16 + 第二跳机制抽查（retrieval.py 离线诊断）
#   逐题对照 vs R96B：翻盘/回退清单（噪声 ±4 解读纪律）
```

## 4. 离线机制抽查（SC-7 辅助）

```bash
# retrieval.py 对 multi_hop 题做机制抽查：确认第二跳查询构造含第一跳结论值；
# 离线数据只做机制验证，不替代全臂 SC-7 出数。
```

## 5. 收尾

```bash
make quality          # SC-8：全绿，失败基线为空
```

交付物：全臂目录（jsonl + report.md）、机制/attribution/逐题对照报告、交付报告（对照 SC-1~SC-8 逐项给证据）。
