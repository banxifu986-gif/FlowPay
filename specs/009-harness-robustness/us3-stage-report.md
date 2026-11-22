# 009 US3 阶段报告（T016–T019）

分支：`feature/009-us3`（worktree Flow-009-us3）；完成时间：2026-11-23 01:53。

## 交付物

| 任务 | 提交 | 内容 |
|---|---|---|
| T016 | d06535a | `workflows/multi_agent/citation_check.py`：引用标记解析（记录编号复用 core `RECORD_ID`、片段引用 `[doc:...]`）+ 无标记断言确定性降级（统一口径「未能核实」）；纯本地零模型调用；降级优先，不触发定向重写 |
| T018 | 6326ef5 | `scripts/benchmark/run.py`：`Row.unverified` 字段 + 分型报表未证实统计行；`clarification.py`：`unverified_rounds` 逐题统计 + 报表行；既有指标口径零改动 |
| T017 | bacaca5 | `resolve_response` 接入校验：answer 正文输出前降级；clarify 正文（系统确定性拼接 + pending 场景）不改写；T007 函数体拆为 `_resolve_decision` 原样保留 |

## Rebase 时间点

- rebase 执行于 2026-11-23 ~01:45，基点 = integration 分支 `feature/009-harness-robustness` 6457e6e（含 T003–T007 全部脊柱提交）。
- T016/T018 两个提交无冲突重放（d06535a、6326ef5）；T017 在 rebase 之后的 HEAD 上实施。

## 验收证据（T019）

- 容器全量 pytest（本 worktree 挂载，`FLOWPAY_DATASET_ROOT=/dataset`）：**581 passed + 5 skipped**；失败仅 `test_checks.py::test_collection_preparation[missing/mismatch]` 4 例——已用主仓干净代码同挂载方式复现同样的 4 例，确认为 dataset release manifest 挂载布局的环境性差异，非新债（失败基线为空纪律保持）。
- ruff check / format：backend/src + backend/tests + scripts 全绿（205 files formatted）。
- basedpyright：5 errors 全部位于 `test_failure_degrade.py` 的既有夹具（RecordingTracer 未实现 Tracer 协议、DeterministicSearch 缺 @override）——在 T007 干净代码上复现同样的 5 errors，属脊柱会话引入的既有债，本分支未触碰；未擅自修复（纪律：不扩大修改范围）。
- 宿主机限制：`test_routing.py` 等依赖 fcntl 的文件不可收集（129c505 已记录的容器-only 结论），全部在容器内验证。

## SC-5 单测证据

- `test_citation_check.py`（16 例）：带标记（记录编号/片段引用）放行、无标记断言降级、混排只降级无标记句、标记不跨句、幂等、未证实口径词豁免、疑问句/引导语/请求句豁免、错误码不算引用、列表前缀保留、空文本与分隔线、统一口径词面。
- `test_failure_degrade.py`（US3 增 4 例）：模型作答裸奔断言输出前降级、带标记原样、clarify 正文不改写、synthesize 集成路径降级后成为最终作答且 `clarification.decided` mode=answer。
- `test_benchmark_harness.py::test_render_reports_unverified_counts`：报表列出「未证实口径：N 处（M 题：…）」，无命中时不出现该行（既有指标口径不变）。
- `test_benchmark_clarification.py::test_render_reports_unverified_rounds`：澄清报表增「未证实口径：N 轮（M 题）」行。

## citation_check 误判边界用例清单

| 边界 | 用例 | 判定 |
|---|---|---|
| 标记不跨句 | 相邻句编号不替本句作保 → 降级 | 已固化 |
| 幂等 | 已含「未能核实」的句子不二次降级 | 已固化 |
| 未证实口径词豁免 | 「未能确认/待确认/无法确认/没有查到」不降级 | 已固化 |
| 疑问句 | 「是否已经退款？」不降级 | 已固化 |
| 引导语 | 冒号结尾（已查到的结论：）不降级 | 已固化 |
| 请求句 | 「请联系技术支持」不降级（"支持"已从线索词移除） | 已固化 |
| 错误码 | FP-4021 不是引用标记，无标记仍降级 | 已固化 |
| 客套话 | 裸「已/已经」不入选线索词（"已根据报告回答"不误伤，T007 既有测试回归发现） | 已固化 |
| 宽松判定 | 无线索词句子放行（宁可漏判） | 已固化 |
| 结构保留 | 列表前缀、行结构与空行原样 | 已固化 |

## 设计取舍备注

- 降级优先于定向重写：本地降级已消灭裸奔断言，定向重写路径（FR-6 的"或"支）不触发，零额外模型调用（费用假设成立）。
- 校验仅作用于 answer 模式：clarify 轮必伴随 pending（FR-7 拍板未证实口径仅约束无 pending 场景），且其正文由 clarify_body 确定性拼接、自带「未能确认」豁免词。
- scripts 侧按词面「未能核实」判定，不 import workflows 层（scripts 只投影事件与正文，分层纪律）。

## 遗留观察（备注，不动手）

1. `test_failure_degrade.py` 的 RecordingTracer/DeterministicSearch 类型债（5 errors，T007 引入）——建议脊柱会话补 Protocol 化或 @override。
2. `degraded_answer`（FR-12 占位）与 citation_check 的统一口径词面已一致，无需再接。
3. 容器挂载本 worktree 的验证通道（PYTHONPATH + FLOWPAY_DATASET_ROOT）未入库为脚本，每次手工拼命令；如后续会话频繁用可考虑固化。
