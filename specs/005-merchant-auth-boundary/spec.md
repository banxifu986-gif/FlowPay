# 规格：005 商户登录与数据边界

状态：已批准（2026-11-16，经 grill-me 逐分支拷问确认；实施交由 Pi 按本规格执行，随分支 feature/005-merchant-auth-boundary）
前置：specs/004-cross-session-memory 已收尾；上线目标形态为「先内网」——TLS/域名/反向代理留位不实现。
参考设计：Claude Code 权限边界（边界由服务端强制、提示词不可改写）→ 本规格的 scope 注入与 ACL 过滤；Stripe 式商户账号模型（账号绑账户，账户绑数据）。

## 背景

现状是「演示 + 裸 API」：无任何鉴权；SQL/日志工具的 `account_id` scope 由对话提供，谁都能查任意商户数据；跨会话记忆全局共享；检索对全部文档一视同仁。上线形态要求：外部商户登录后查自己的数据，平台知识文档分内外两级可见。本规格是安全内核，006（网关加固与上线增强）依赖本规格落地。

## 目标

1. 登录门禁：预置演示账号（商户+内部两角色），全部 `/api/*` 要求 Bearer 令牌。**未登录请求从「可用」变为 401 是有意的行为变更**，是「默认行为逐字不变」原则的唯一例外，写明。
2. 商户数据边界：商户会话的业务查询 scope（`account_id`）由服务端强制注入并覆盖对话值；查他人单号按无记录处理，不泄漏存在性。
3. 文档 ACL：知识文档分 `external|internal` 两级，检索按登录角色服务端过滤。
4. 记忆隔离：跨会话记忆按商户 owner 隔离；商户默认开启、内部默认关闭（保护 300 题基准评测不被记忆污染）。

## 用户故事

- **US1（P0）账号模型与登录**：预置账号配置文件（仿 `backend/configs/local.toml` 模式，git 忽略），字段：username / 密码哈希（stdlib `hashlib.pbkdf2_hmac`，**不加新依赖**）/ role（`merchant|internal`）/ account_id（商户必填、内部为空）；提供种子脚本。`POST /api/auth/login` → stdlib HMAC 签名令牌（密钥来自 env，24h 过期，无服务端会话表）；全部 `/api/*` 端点要求 `Authorization: Bearer`（含 SSE `/stream`）；401/403 结构化错误体。验收：错误口令 401；无令牌访问任意业务端点 401；令牌过期 401。
- **US2（P0）scope 服务端注入**：core 层业务无关落地——`backend/policies/sources.toml` 已声明各源 scope 列（注释明确「隔离由 SQL 保证，不靠提示词」），新增「会话主体绑定 scope 值」机制：商户会话所有 SQL/日志工具的 `account_id` scope 在工具调用边界由服务端注入并**覆盖对话值**（LLM/用户不可改写）；`environment`（生产/沙箱）仍是对话条件，不属于强制注入范围；内部账号 scope 照旧由对话提供（现状保留）。验收：商户 A 会话内对话给出商户 B 单号 → 返回空/无权限语义，不泄漏 B 数据存在性；事件日志可见注入后实际执行的 scope。
- **US3（P0）文档 ACL**：文档元数据加 `audience: external|internal`；导入管线（`scripts/ingestion/`）透传入 pgvector 元数据；`search_documents` 按登录角色服务端过滤（internal=全部，merchant=仅 external）；既有 100 篇默认标 external，明确内部文档单独标注。验收：商户会话检索不到 internal 文档；内部账号全量可见。
- **US4（P0）记忆按商户隔离**：`memory_notes` 加 owner 维度（=商户 account_id）；存取/召回/淘汰（004 的 ≤200 上限改为 per-owner）全部按 owner 收敛；内部账号默认关闭记忆（`memory_recall_char_budget=0` 路径，零记忆读写事件），商户默认开启。验收：商户 A 的排查结论不出现在商户 B 的召回；内部账号会话零记忆事件（基准纯净）。
- **US5（P1）会话归属与可见性**：sessions 记录 user_id；商户的列表/导出/SSE 只见自己的会话；内部看全部；事件日志记 user 归属。验收：商户 B 读取商户 A 会话 → 404/403，不泄漏存在性。
- **US6（P1）前端登录与身份**：登录页、token 存储、401 跳登录、身份/角色展示；登录后功能布局不变（数据边界由后端保证）。验收：手动全链路冒烟：登录 → 提问 → SSE → 退出。
- **US7（P2）测试链路兼容**：`scripts/demo.py` 契约假后端保持无鉴权（它是 mock，不门禁）；smoke/benchmark 脚本加内部账号登录 helper。验收：300 题基准经内部账号运行，分数不低于 main 基线。

## 范围外

- 自助注册、邮箱验证、密码找回、多内部账号管理界面、OAuth/SSO
- TLS/域名/反向代理（内网形态，006 仅预留反代位）
- pgvector 存账号/令牌（预置账号量级用配置文件即可）
- 文档可见性细粒度（仅两级，无按商户分组文档）
- 商户间共享会话或协作；前端管理后台
- 限流/配额/反馈（归 006）

## 验收标准（总）

- 新增隔离测试套件：401/403、跨商户数据不可见、ACL 检索过滤、scope 不可被对话改写。
- `docker compose run --rm --no-deps backend python -m pytest backend/tests -q` 与 `make quality` 全绿。
- **300 题基准（内部账号）不回归 = 硬门禁**。

## Clarifications（已与 Ban 确认）

1. 产品定位：支付平台商户自助支持门户；知识库固定由平台方维护，用户不上传文档。
2. 用户 = 外部商户，登录后查自己遇到的问题；账号为预置演示账号（用户名+密码），无自助注册。
3. 账号分商户/内部两角色：商户绑死 account_id、只见 external 文档；内部不绑 account_id、见全部文档——基准与冒烟走内部账号原样跑通。
4. 文档可见性分内外两级，检索层服务端过滤，不做提示词约定。
5. 部署先内网上线形态，网关落在应用层（FastAPI 中间件）。
6. 记忆商户开、内部关；隔离是安全必做项（防商户间结论泄漏）。
7. 未登录 401 是有意的行为变更（默认行为逐字不变原则的唯一例外）。
8. `environment`（生产/沙箱）仍是对话条件，商户查的是自己账号的两套环境。
9. 密码哈希与令牌签名全部用 stdlib，不新增依赖。
10. 实施交由 Pi：先按 spec-kit 流程补 plan/tasks 工件，再逐 US 实施。
11. internal 文档清单（2026-11-16 补）：数据集发布清单经核实现无内部文档子集（100 篇全属 `published_knowledge/agent_knowledge` 组）→ 全部标 external（缺省），ACL 机制由测试夹具覆盖；**免重建集合**——查询侧把缺失 `audience` 视为 external，或直接在 DB 回填元数据，实施者选实现最小者，不重算 manifest/sha。未来真需要内部文档时从数据集源头加 `internal_knowledge` 分组（manifest 的 group visibility 机制已支持），本轮不做。
12. SSE 令牌传递（2026-11-16 补）：走 `Authorization` header，前端改 fetch 流式读取；`/stream` 不接受 query 参数令牌（令牌不进 URL/访问日志）。
13. 基准门禁执行口径（2026-11-16 定，同日修订）：~~全量双跑~~ → **以抽样口径收尾**（PER_TYPE=2、12 题、同数据集/题库/知识集合/嵌入模型：feature 2/12 任务通过 + 3/12 证据全命中 vs main 基线 0/12 + 1/12，不回归且略优）。全量双跑的两臂容器已停止，部分产物留存 var/；单题成本（~19 次模型调用 ×7s + 5 次 CPU 嵌入 ×9s，约 100–150s/题，全量约 8–12h/臂）记录于 tasks.md，供未来需要全量时评估。
14. `make quality` 既有 4 处红项（EXE002、两处 I001、prettier 两文件）授权修复以让门禁变绿，报告中注明属既有问题、为门禁修复。
15. 独立审核回流（2026-11-16）：
    - **F1（必须修）**：`logs.delivery_attempts` 的 `filter_fields` 无 `account_id`，scope 注入在该路径失效。修法＝**服务端反查归属**：merchant 主体查询前先经 events（event_id→account_id/environment）反查 delivery_id 归属，非本账户一律空结果不泄漏存在性；实现沿用 `webhook_deliveries` 经 events JOIN 取 scope 列的同一思路，在 core 层做成通用机制而非业务特判；覆盖后 scope 记入事件。
    - **F2（必须修）**：容器/`make up` 部署路径接线——`local.toml` 加 `accounts_path`、compose 注入 `FLOWPAY_ACCOUNTS_PATH` 与 `FLOWPAY_AUTH_TOKEN_SECRET`、`.env.example` 追加两个键（模板文件，允许编辑）、quickstart 补种子步骤；fail-fast 行为保持不变。
    - **F3（必须修）**：事件载荷脱敏——`run.failed`/`knowledge.error`/`document.error`/`memory.recall_failed`/`memory.extract_failed` 等写入会话事件日志的异常文本降级为安全摘要（类别，不含 DSN/主机/SQL/路径），完整异常仅入服务端日志；不得破坏 004 reactive compact 的错误分类路径。
    - **F5/F6（防御加固）**：merchant 令牌空 `account_id` 在校验层拒绝；`knowledge/store.py` 元数据过滤键加白名单断言。
    - **F4/F7 记录为已接受风险**（SSE 连接后不重鉴权；无 audience 标记文档默认 external——与免重建决策配套）；登录限流归 006。
16. 提交策略（2026-11-16）：全部修复完成并复核后**暂不提交**，暂存区保持现状（含 exec-bit），提交时机由所有者另行决定。
17. 13 个既有测试失败（manual-loop 参数组 + multi-agent 两例）在 005 收尾后**另立任务排查**，目标下轮门禁真全绿，不进 005 范围。
