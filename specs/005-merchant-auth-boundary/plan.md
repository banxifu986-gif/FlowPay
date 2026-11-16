# 实施计划：005 商户登录与数据边界

需求唯一来源见 spec.md；本文件记录落地路径、分层接线与批次。

## 批次

| 批次 | 内容（US） | 门禁 |
|---|---|---|
| P0-1 | US1 账号模型与登录 | 容器 pytest 失败集合不超基线 + `make quality` |
| P0-2 | US2 scope 服务端注入 | 同上 |
| P0-3 | US3 文档 ACL | 同上 |
| P0-4 | US4 记忆按商户隔离 | 同上 |
| P1 | US5 会话归属与可见性 | 同上 |
| P1 | US6 前端登录与身份 | 同上 + 手动冒烟 |
| P2 | US7 测试链路兼容 + 300 题基准（内部账号） | 基准分数不低于 main 基线 |

基线：容器 pytest `13F/284P/3S`（2026-11-16 实测）；失败集合属既有问题，不改断言。

## 分层接线（对齐 AGENTS.md）

- `core/auth/`：`contracts`（Principal/AuthError）、`passwords`（pbkdf2_hmac）、`tokens`（HMAC）、
  `accounts`（配置文件解析）、`service`（令牌校验实现）。不依赖 workflows/router/bootstrap/scripts。
- `core/http/app.py`：`create_app(settings, workflow, authenticator=None)` 加统一门禁中间件；
  `authenticator=None` 时不门禁（`scripts/demo.py` 契约假后端专用）。
- `core/execution/`：`SessionManager` 公开方法收 `Principal`；`_execute` 按角色构造运行时边界
  （scope 覆盖、文档可见性、记忆 owner、会话归属）。core 只认「scope 列名→强制值」这种通用形态。
- `core/data_sources/tools.py`：`sql_tool/log_tool` 增加 `scope_overrides`；注入发生在工具调用边界，
  先覆盖再拼 target/记录事件，因此事件里的 parameters 就是实际执行的 scope。
- `core/memory/`：`memory_notes` 加 owner 列，存取/召回/淘汰全部按 owner 收敛。
- `core/knowledge/`：`audience` 元数据（缺省 external），检索与整篇读回都按可见范围过滤。
- `workflows/`：只把 `runtime.scope_overrides` 原样转交工具，不判断角色。
- `bootstrap.py`：`create_authenticator(settings)` 装配令牌校验；`router.py` 不变。
- `scripts/`：`accounts.py` 种子脚本；`auth.py` 登录 helper；demo 保持无鉴权。

## 令牌与账号

- 账号文件 `backend/configs/accounts.toml`（git 忽略，仿 `local.toml` 由脚本生成）。
- 密码：`hashlib.pbkdf2_hmac`（sha256，迭代数与会话内解析参数），只存盐与派生值。
- 令牌：stdlib HMAC-SHA256 签名的 `base64url(payload).base64url(sig)`，payload 含
  `sub/role/account_id/exp`；密钥来自 `FLOWPAY_AUTH_TOKEN_SECRET`，有效期 24h，无服务端会话表。
- 未登录 401 是有意行为变更（spec 目标 1）；`scripts/demo.py` 不门禁。

## 测试策略

- 新增 `backend/tests/core/test_auth.py`：口令错误 401、无令牌访问各端点（含 `/stream`）401、
  过期令牌 401、商户改不了 scope、跨商户会话 404、ACL 过滤、记忆 owner 隔离、内部零记忆事件。
- 既有用例统一走 `tests/core/auth_fixture.py` 的登录 helper（或注入的固定账号策略），
  断言不改，只补令牌。
- 手动冒烟：登录 → 提问 → SSE → 退出（quickstart.md）。

## 风险

- 旧知识集合缺 `audience` 键：查询侧按 `DEFAULT_METADATA` 视为 external，因此不需要重建。
- `EventSource` 无法自定义请求头：前端订阅改为 fetch 流，`/stream` 只认 `Authorization`。
- 内部账号默认关记忆，基准纯净性依赖「按角色关闭」而不是全局配置。
