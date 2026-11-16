# 检查表：005 安全内核

## 登录门禁

- [ ] 除 `POST /api/auth/login` 外，所有 `/api/*` 无 `Authorization: Bearer` 返回 401
- [ ] SSE `/api/sessions/{id}/stream` 同样门禁（不因浏览器限制留后门）
- [ ] 口令错误 401，响应体不区分「用户不存在 / 口令错误」
- [ ] 令牌过期 401；签名被改动 401；`exp` 缺失 401
- [ ] 令牌密钥只从环境变量读取，不落配置快照、不进日志
- [ ] 账号文件被 git 忽略，只存盐与派生值
- [ ] `scripts/demo.py` 契约假后端保持无鉴权（仅 mock 用途）

## 数据边界

- [ ] 商户会话 `account_id` 由服务端覆盖，对话值不生效
- [ ] 事件 `source.request.parameters` 是覆盖后的实际条件
- [ ] 查他人单号返回空行（不返回 404/403、不泄漏存在性）
- [ ] `environment` 仍由对话提供，不被强制覆盖
- [ ] 商户检索与整篇读回都限定 `audience=external`
- [ ] 记忆存取/召回/淘汰按 owner 隔离，跨商户不可见
- [ ] 内部账号零记忆读写与事件
- [ ] 商户列表/详情/导出/SSE/删除只见自己；越权读取 404
- [ ] 旧会话（无 `user_id`）不被商户看到

## 回归

- [ ] 容器 `python -m pytest backend/tests -q` 失败集合不超基线
- [ ] `make quality` 全绿（ruff / basedpyright / pytest / 前端）
- [ ] 300 题基准经内部账号运行，分数不低于 main 基线
