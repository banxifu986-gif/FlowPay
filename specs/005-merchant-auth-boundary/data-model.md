# 数据模型：005 商户登录与数据边界

## 账号（本地配置文件，git 忽略）

`backend/configs/accounts.toml`：

```toml
token_ttl_hours = 24              # 可选；缺省 24

[[accounts]]
username = "merchant_demo"
role = "merchant"                 # merchant | internal
account_id = "acct_..."           # 商户必填，内部必须为空
password_hash = "pbkdf2_sha256$210000$<salt_b64>$<digest_b64>"
```

约束：
- `role=merchant` 必须有 `account_id`；`role=internal` 必须没有。
- 用户名唯一；口令校验用 `hashlib.pbkdf2_hmac`，比较用常量时间比较。
- 文件不含明文口令；种子脚本 `scripts/accounts.py seed` 生成。

## 主体（Principal）

| 字段 | 类型 | 说明 |
|---|---|---|
| `username` | str | 账号文件里的用户名，同时是会话归属 `user_id` |
| `role` | `merchant` \| `internal` | 决定 scope 注入、文档可见性、记忆开关、会话可见范围 |
| `account_id` | str \| None | 商户绑定的账户；内部为空 |

## 令牌

`base64url({"sub","role","account_id","exp"}) + "." + base64url(HMAC-SHA256)`，密钥取
`FLOWPAY_AUTH_TOKEN_SECRET`。校验失败一律 401（`{"detail","code"}` 结构），不区分「不存在」与「无效」。

## 会话（事件日志）

`session.created` 载荷新增 `user_id`（令牌主体用户名）。会话列表按 `user_id` 过滤：
商户只见自己，内部（`user_id=None` 语义）见全部。读取不属于自己的会话按 `SessionNotFound`（404）。

## 记忆（memory_notes）

| 列 | 说明 |
|---|---|
| `owner` | 商户 `account_id`；内部不写记忆，因此该表只有商户 owner |
| 其余列 | 同 004（note_id/session_id/workflow/created_at/keys_json/conditions_json/body/embedding/body_chars） |

- 单键替换、`max_notes` 淘汰、键匹配、语义兜底全部限定在同一 `owner` 内。
- 内部账号：`memory_recall_char_budget=0` 路径，零读写、零记忆事件。

## 文档元数据

`documents.metadata` 增加 `audience`：`external`（缺省）| `internal`。导入解析时补齐缺省值；
查询侧把缺键视为 `external`（`DEFAULT_METADATA`），所以既有集合同样可按角色过滤，无需重建。

| 角色 | `search_documents` | `read_document` |
|---|---|---|
| merchant | `filters={"audience":"external"}` | 同上（否则整篇读回绕过 ACL） |
| internal | 无过滤 | 无过滤 |

## 业务查询 scope

`sources.toml` 的 `scope` 列名不变。商户会话在工具边界强制覆盖 `account_id = <本人 account_id>`；
`environment` 仍由对话给出。事件 `source.request.parameters` 记录覆盖后的实际条件。
