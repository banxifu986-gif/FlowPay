# 实施计划：006 网关加固与上线增强

需求唯一来源见 spec.md；本文件记录落地路径、分层接线、批次与验收口径。

## 批次

| 批次 | 内容（US） | 门禁 |
|---|---|---|
| P0-1 | US1 用量守卫 | 容器 pytest 失败集合与基线逐条一致 + `make quality` + 专项用例 |
| P0-2 | US2 结构化错误与降级 | 同上 |
| P0-3 | US3 上下文管理默认启用 | 同上 + 基准显式声明配置后不回归 |
| P1 | US4 混合检索 | 同上 + 既有检索验证脚本 + 离线对比数据 |
| P1 | US5 反馈闭环 | 同上 + 反馈事件可查询、后续上下文不含反馈 |
| P2 | US6 内网全链路冒烟 | `scripts/smoke_006_launch.py` 一键通过 |

基线：容器 pytest `13F/332P/4S`（2026-11-16 实测，13 条既有失败另立任务清偿）。
500 基线：抽样基准 `PER_TYPE=2`、内部账号、feature 2/12 任务通过、3/12 证据全命中；
main 0/12、1/12（`var/benchmark/20261116-122818-8a9bdc/`）。

## 分层接线（对齐 AGENTS.md）

- `core/config/`：Settings 增用量守卫、失败恢复、混合检索配置项；缺省值见 data-model.md。
- `core/event_log/`：协议与 SQLite 实现增日回合计量查询（复用 `run.started` 事件，
  不新建计量表，不把业务循环藏进存储）。
- `core/execution/manager.py`：提交前在锁内做用量守卫（每商户日回合 + 并发 + 全局日上限），
  超限抛 `QuotaExceeded`；执行失败按分类重试、记录 `run.retry`，终态载荷带友好文案。
- `core/execution/failures.py`：失败分类与恢复策略映射（纯函数，不依赖工作流）。
- `core/execution/errors.py`：`QuotaExceeded` 与 HTTP 429 结构体一致。
- `core/http/app.py`：429 异常处理；新增 `POST /api/sessions/{id}/feedback`。
- `core/knowledge/`：`search_hybrid`（pg_trgm 关键词路 + 向量路 RRF）留在存储实现，
  `KnowledgeClient` 显式开关；Evidence 溯源字段不变。
- `core/chat/`：反馈事件不进入 `session_view` 消息与检查点；前端投影单独读取。
- `bootstrap.py` / `resources.py`：装配混合检索与用量限制配置，不新增依赖。
- `scripts/database.py`：迁移里 `CREATE EXTENSION pg_trgm` 与 GIN 索引。
- `scripts/benchmark/run.py`：显式声明治理配置（默认关闭，`--governance` 开启），
  避免默认变导致的隐性偏差。
- `frontend/`：反馈按钮与附言，事件驱动；不把反馈写入聊天上下文。

## Clarifications 落地

1. 四项增强 + 上下文默认启用全部实施。
2. 基准不回归为硬门禁；混合检索提升只记录离线对比数据，不设硬线。
3. 关键词路用 pg_trgm；迁移内创建扩展，不新增 Python/前端依赖。
4. 反馈只进事件日志，不注入对话上下文。
5. 守卫粒度：每商户日回合 + 并发 1 + 全局日上限；默认值见 data-model.md，可配置。

## 风险与备注

- 005 已接受风险 F4（SSE 连接后不重鉴权）、F7（缺 audience 默认 external）本轮不修。
- 混合检索只在生产配置开启；测试直接构造 `KnowledgeClient` 时保持纯向量路径，
  避免既有 PostgreSQL 用例的 `score≈1` 断言被 RRF 分数改写。
- 用量守卫计量来自事件日志，跨进程以同一数据目录为界；不是分布式配额服务。
- 基准默认关闭新治理项以保持与 005 抽样同口径；开启治理的对照数据另行记录。
