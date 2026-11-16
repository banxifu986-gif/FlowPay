# 任务：006

批次与门禁见 plan.md。每个 US 完成后跑该 US 验收项，通过再进下一个。

## P0

- [x] T001 基线核对：容器 `13F/332P/4S`（2026-11-16 实测，失败集合逐条记录）
- [x] T002 `core/config/settings.py` 增用量守卫/失败恢复/混合检索配置；`local.toml` 回填
- [x] T003 `core/event_log/` 增按 UTC 日计量 `run.started` 的查询（商户 + 全局）
- [x] T004 `core/execution/manager.py` 守卫：商户日回合 + 并发预留/释放 + 全局日上限；
  `QuotaExceeded` + HTTP 429 结构化错误；默认值 `quota_merchant_daily_turns=200`、
  `quota_global_daily_turns=5000`、`quota_merchant_concurrency=1`（**待真机校准**）
- [x] T005 验收 US1：预算耗尽 429 且文案可读；并发第二会话被 429；配置 0 可关闭
- [x] T006 `core/execution/failures.py` 分类映射（超时/限流/上下文超长/配额/模型 5xx）
- [x] T007 管理器执行失败按分类退避重试、`run.retry` 留痕、终态友好文案与统一 shape
- [x] T008 验收 US2：注入各类故障分类正确、重试按映射执行、文案友好；429/401/403 shape 一致
- [x] T009 `local.toml` 回填 `context_*` / `memory_*` 校准值并默认开启
- [x] T010 基准脚本显式声明治理配置（默认关闭、`--governance` 开启并写入报表）
- [x] T011 验收 US3：默认配置下投影/压缩事件可见路径有测试覆盖；基准抽样不回归

## P1

- [x] T012 迁移 `0003_trigram`：`pg_trgm` + GIN 索引
- [x] T013 `PostgresVectorStore.search_hybrid`：关键词路 + 向量路 + RRF；溯源字段不变
- [x] T014 `KnowledgeClient` / 资源配置接线混合检索；测试直构客户端保持纯向量
- [x] T015 验收 US4：既有检索验证通过、专项单测覆盖融合与去重、离线对比数据记录
- [x] T016 `manager.record_feedback` + `POST /api/sessions/{id}/feedback`；append-only 事件
- [x] T017 前端消息级 👍/👎 + 附言；反馈状态由事件投影
- [x] T018 验收 US5：反馈可查询；后续轮次上下文与检查点不含反馈正文

## P2

- [x] T019 `scripts/smoke_006_launch.py`：登录 → 提问 → SSE → 反馈，一键通过
- [x] T020 compose 预留反向代理注释位（不实现）；quickstart/checklists 补步骤
- [x] T021 验收 US6：冒烟脚本通过

## 收尾

- [x] T022 门禁：容器 pytest 失败集合与基线逐条一致、新增全过；ruff/basedpyright/前端三件套全绿
- [x] T023 抽样基准（PER_TYPE=2，内部账号）不回归；混合检索离线对比数据入报告
- [x] T024 交付报告：US 验收、默认值、文件清单、遗留风险（不 commit/push）


## 收尾记录（2026-11-16）

- 容器 pytest：`13F/347P/5S`，13 条失败与基线逐条一致；新增 15 条专项用例全过。
  另一次全量曾出现 `test_parallel_workers_finish_no_later_than_serial` 抖动
  （005 已记录的并发负载现象），单独运行通过。
- ruff check / format --check：全绿（191 files）；basedpyright：0 错 0 警。
- 前端：prettier 全绿、vitest 55/55、build 成功。
- 迁移：`0003_trigram` 已在本地知识库执行（pg_trgm + GIN 索引）。
- US6 冒烟：`scripts/smoke_006_launch.py` 对运行中的 compose 后端通过
  （登录 → 提问 → SSE → 反馈）。
- 抽样基准（PER_TYPE=2，内部账号，multi-agent）：
  - 门禁口径（基线配置、纯向量）：2/12 任务通过、3/12 证据全命中
    （`var/benchmark/20261116-142705-55d7c7/`）；
  - 混合检索对照：1/12 任务通过、3/12 证据全命中
    （`var/benchmark/20261116-150316-748fd7/`，另两次 0/12、3/12 与 2/12、3/12 波动）。
  - 结论：证据全命中与基线持平；任务通过数受模型波动影响，混合检索本批未观察到稳定提升，
    作为目标非硬线记录。融合已加「向量路保底半名额」避免关键词噪声挤掉向量命中。
- 限流默认值：商户 200 回合/UTC 日、内部账号 500 回合/UTC 日、并发 1、
  全局 5000 回合/UTC 日，均待真机校准。
- F1 修复后 `--governance` 基准：`var/benchmark/20261116-155644-6d1641/`，
  报表标注“检索路径：纯向量”且运行日志确认实际为纯向量；该臂启用 006 其余默认治理项，
  结果 0/12 任务通过、2/12 证据全命中，与基线口径（0/12… 基线臂 2/12、4/12）
  不用于门禁比较，按单次模型波动记录。门禁仍以基线配置臂为准。
- 审核回流后复跑：容器 pytest `13F/352P/5S`；ruff/basedpyright/前端三件套全绿；
  `scripts/smoke_006_launch.py` 复跑通过（含反馈重复提交幂等断言）。


## 审核回流（Clarifications 6–8，2026-11-16）

- [x] F1 基准口径：`BASELINE_GOVERNANCE` 补 `failure_retry_attempts/delay`，
  `--governance` 显式 `knowledge_hybrid=False`；打印与报表按实际 settings 标注检索路径。
- [x] F4 内部账号个体日上限：`quota_internal_daily_turns`（默认 500，0 关闭），
  429 语义与商户一致；测试覆盖内部超额与商户不受影响。
- [x] F5 反馈去重：同一 `(session_id, run_id, user)` 只追加一次、重复幂等；
  跨主体互不阻挡；运行中轮次 409。
- [x] F2/F3 记录：单进程配额窗口与并发计数仅单 worker；多实例前需落事件日志 + 跨进程锁。
- [x] F6/F7/F8 接受记录：限流可重试与 connect 名字启发式、计数 JOIN 假设、
  迁移需 `CREATE EXTENSION` 权限（运维注意）。
