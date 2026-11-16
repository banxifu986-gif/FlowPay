# 任务：005

批次与门禁见 plan.md。每个 US 完成后跑该 US 验收项，通过再进下一个。

## P0

- [x] T001 基线核对：容器 `13F/284P/3S`（2026-11-16 实测）
- [x] T002 `core/auth/`：Principal/AuthError、pbkdf2 口令、HMAC 令牌、账号文件解析
- [x] T003 Settings 增 `accounts_path` / `auth_token_secret_env`，loader 解析路径（令牌有效期在账号文件）
- [x] T004 `scripts/accounts.py seed`：生成 git 忽略的账号文件（商户 + 内部）
- [x] T005 `create_app` 门禁中间件 + `POST /api/auth/login` + 401/403 结构体；demo 走 `authenticator=None`
- [x] T006 验收 US1：错误口令 401、无令牌各端点（含 `/stream`）401、过期令牌 401
- [x] T007 `core/data_sources/tools.py` scope_overrides 注入 + `AgentRuntime` 转交 + 两个 workflow 接线
- [x] T008 验收 US2：商户 A 会话给 B 单号 → 空结果；`source.request` 记录实际 scope
- [x] T009（代码侧完成：缺省 external + 取值校验；数据集打标待清单） `parse_document` 补 `audience` 缺省并校验取值；导入管线透传（含重建集合说明）
- [x] T010 检索与整篇读回按角色过滤（`VectorStore.read_document` 加 filters）
- [x] T011（夹具集合验证；真实集合需重建） 验收 US3：商户检索不到 internal；内部全量可见（夹具集合）
- [x] T012 `memory_notes` 加 owner：save/match_keys/search_semantic/淘汰按 owner 收敛；旧库补列
- [x] T013 记忆按角色开关：内部零读写、零记忆事件；商户默认开启
- [x] T014 验收 US4：A 的结论不出现在 B 召回；内部会话零 `memory.*` 事件
- [x] T015 `session.created` 记 `user_id`；列表/详情/导出/SSE/删除按归属收敛（404 不泄漏）
- [x] T016 验收 US5：商户 B 读 A 会话 404；内部见全部
- [x] T017 前端：登录页、令牌存储、401 跳登录、身份/角色展示
- [x] T018 验收 US6：门禁版契约后端 HTTP 冒烟（登录→提问→SSE→退出后 401）+ 前端 vitest 覆盖登录/401/SSE 订阅；浏览器人工冒烟留给项目所有者
- [x] T019 `scripts/auth.py` 登录 helper；demo 无鉴权；smoke/benchmark 接线
- [ ] T020 收尾：pytest 与基线一致（13F/323P/4S，失败集合同基线）、ruff/basedpyright/prettier/前端测试与构建全绿；300 题抽样对照 feature 2/12 vs main 0/12（任务通过），全量双跑待授权执行

## 收尾记录（2026-11-16）

- 已按 Ban 的确认回填：文档全部 external，查询侧缺键兜底，不重建集合。
- SSE 采用 fetch 流 + `Authorization`（`EventSource` 不支持请求头）。
- 既有门禁红项已修：EXE002（scripts 加 shebang 与可执行位）、
  两个脚本 I001、prettier 报的 App.tsx / chat-panel.tsx、ruff 0.16.6 下 20 个既有
  未格式化文件（纯格式，无逻辑改动）、`graph.py` 的 langgraph stub 警告。
- pytest 仍为 13 个既有失败（与基线同一集合，未改断言）。
- 基准：抽样 feature 2/12、main 0/12（任务通过）；全量 300 题双跑未执行。

## 审核回流（Clarifications 15–17）

- [x] F1 `logs.delivery_attempts` 归属反查：`sources.toml` 声明 ownership + `sql.webhook_delivery_ownership`；
  core 按声明反查 scope、不符返回空行、拒绝路径只记主体自身绑定值；
  测试 `tests/core/test_log_ownership.py`（他人→空、自己→有行、内部不变、审计含解析 scope）。
- [x] F2 部署接线：`local.toml` 加 `accounts_path`、compose 注入 `FLOWPAY_ACCOUNTS_PATH` 与
  `FLOWPAY_AUTH_TOKEN_SECRET`（空值不阻塞 compose，应用 fail-fast）、`.env.example` 追加；
  quickstart 补 `make up` 验证；测试 `test_bootstrap_fails_fast_without_accounts_or_secret`。
- [x] F3 事件载荷脱敏：新增 `core/errors.py:error_summary`；run.failed / knowledge.error / document.error /
  memory.recall_failed / memory.extract_failed / source.error / tool.error / model.error / embedding.error /
  context.compaction_failed 只记类别，完整异常入服务端日志；reactivate compact 分类路径未动；
  测试 `tests/core/test_error_redaction.py`（事件不含 DSN，日志保留原文）。
- [x] F5 空账户拒绝：令牌校验层拒绝 merchant 空/空白 account_id；管理器 `_require_binding` 兜底。
- [x] F6 元数据过滤白名单：`metadata_condition` 只遍历 `DEFAULT_METADATA` 拼键名，其余走 jsonb 绑定。
- [x] F4/F7 记录为已接受风险（SSE 连接后不重鉴权；无 audience 标记文档默认 external）。
- 披露：`tests/workflows/fixtures.py` 的检索夹具断言放宽为
  `filters in (None, {"audience": "external"})`（specs/005 引入角色过滤后所需）。

## 门禁记录（审核回流后，2026-11-16）

- 容器 pytest：13F/332P/4S，失败集合与基线逐条一致（另有一次 `test_parallel_workers_finish_no_later_than_serial`
  在双基准并发负载下抖动，单独运行通过）。
- ruff check / ruff format --check：全绿（184 files）。
- basedpyright：0 错 0 警。
- 前端：prettier 全绿、vitest 55/55、build 成功。
- 基准按所有者决定采用 **抽样口径**（Clarification #13 的全量双跑以本条为准）：`PER_TYPE=2`、
  multi-agent、feature 与 main 基线各 12 题、同一数据集/题库/知识集合/嵌入模型。
  分数：feature 任务通过 2/12、证据全命中 3/12；main 0/12、1/12。
- 全量双跑曾以 detached 容器启动，已按所有者决定停止（`docker stop`），未产出完整报表；
  单题实测约 100–150s（模型 API ~7s/次、CPU Ollama 嵌入 ~9s/次），全量单臂约 8–12 小时。

## 任务：13 个既有失败清偿与测试基线治理（Clarification #17，2026-11-16 立项）

### 目标

1. 13 个既有失败逐条清偿，容器 pytest 达到 **0F 全绿**。
2. 测试基线口径从「失败集合同基线」改为「**基线必须为空**」——未来任何失败都是新债，当即处理。
3. 断言高度标准沉淀进 AGENTS.md。

### 根因预判（2026-11-16 Claude 审核实证，实施时逐条确认，不得直接照抄）

- **11× `test_agent_loop` manual-loop 参数组**：夹具未配数据源策略，workflow 挂载业务工具时
  `runtime.field_values` fail-fast（`core/execution/runtime.py:93`）。预判为有意行为（缺策略即拒启），
  修法＝夹具补配置；实施时确认该 fail-fast 的引入时点与意图。
- **2× `test_multi_agent`**：轮次结构断言过期——`assert len(model.requests) == 3` 实际 2
  （`test_multi_agent.py:337` 附近）。预判为流程有意演进后的断言未跟；改断言时按外部可见
  行为重写（status / messages / 事件序列），若溯源发现是无意回归则修行为而非断言。
- **`test_parallel_workers_finish_no_later_than_serial`**：双基准并发负载下抖动、单独运行通过，
  顺带评估是否需要加固（放宽时序余量或标注负载前提）。

### 执行要求

- 每条失败先判定「有意变更 → 改断言/夹具」vs「无意回归 → 修行为」，判定依据
  （引入变更的特性/commit）写入本文件判定记录节；禁止不做溯源直接改断言凑绿。
- 断言高度：流程形状断言外部可见结果，不锁内部调用次数/私有结构。
- AGENTS.md 增补两条约定：① 测试断言外部可见行为；② 失败基线必须为空，不滚动。
- 与 006 无依赖冲突：本任务在 `feature/006-launch-hardening` 分支续做（005/006 已先后
  落地该分支），提交时单独成组（test(清偿)），提交时机由所有者发话。

### 验收

- 容器 pytest **0F** 全绿；ruff / basedpyright / 前端三件套全绿。
- 抽样基准口径复跑不回归（12 题抽样，feature ≥ main 基线）。
- 判定记录完整（13/13 有出处）；AGENTS.md 两条约定落地。
