# Quickstart：005 商户登录与数据边界

## 1. 生成账号与令牌密钥（容器/`make up` 同样生效）

```bash
# 生成 backend/configs/accounts.toml（git 忽略）；口令从参数来，不写进仓库
backend/.venv/bin/python scripts/accounts.py seed \
  --merchant-username merchant_demo --merchant-account acct_demo_001 \
  --merchant-password '<自定义>' \
  --internal-username internal_demo --internal-password '<自定义>'
```

脚本同时打印一份 `FLOWPAY_AUTH_TOKEN_SECRET=...`，把它填进 `.env`（不提交；
`.env.example` 里已有同名空白项）。容器部署的两项接线已内置：

- `compose.yaml` 给 backend 注入 `FLOWPAY_ACCOUNTS_PATH=/app/backend/configs/accounts.toml`
  与 `FLOWPAY_AUTH_TOKEN_SECRET=${FLOWPAY_AUTH_TOKEN_SECRET:-}`；
- `backend/configs/local.toml` 声明 `accounts_path = "./accounts.toml"`。

缺账号文件或缺密钥时后端启动即失败（fail-fast），不会以无门禁形态运行；
密钥留空不会阻塞 `docker compose` 解析（本地测试/冒烟不必先备密钥）。
验证：`make up` 后首页出现登录页；`docker compose logs backend` 没有启动错误。

## 2. 文档可见性：旧集合不需要重建

`audience` 缺省 `external`，由导入解析写入新集合；查询侧把缺键视为 `external`
（`core/knowledge/store.py` 的 `DEFAULT_METADATA`），因此既有 100 篇文档所在集合
可以直接按角色过滤，不用重算 manifest/sha，也不用手工回填数据库。

仅当确实要新增 internal 文档时，才在 Markdown front matter 加 `audience: internal`
并从数据集发布侧重新构建集合（本轮数据集中没有 internal 文档）。

## 3. 起服务并手动冒烟

```bash
make run            # 或 make up
```

1. 打开前端，出现登录页；用商户账号登录。
2. 新建会话提问，SSE 正常输出：订阅改用 fetch 流，令牌只走 `Authorization` 头
   （`EventSource` 无法自定义请求头，`/stream` 不接受查询参数令牌）。
3. 顶栏显示用户名与角色徽标；商户只能看到自己的会话与 external 文档。
4. 点「退出」清空本地令牌并回登录页；令牌失效（401）同样自动跳回登录页。

## 4. 脚本链路

```bash
# 冒烟脚本自动登录内部账号（demo 假后端自动跳过）
backend/.venv/bin/python scripts/smoke_runtime.py --url http://127.0.0.1:8000

# 抽样验证内部账号链路（12 题）
make benchmark PER_TYPE=2
# 全量基准（内部身份、记忆关闭，与 main 基线同口径）
make benchmark PER_TYPE=0
```

## 5. 验收命令

```bash
docker compose run --rm --no-deps backend python -m pytest backend/tests -q
make quality            # 在带 backend/.venv 的 Linux/WSL 环境执行
```

Windows 宿主可用等价的容器命令：`ruff check` / `ruff format --check`
（backend/src、backend/tests、backend/migrations、scripts）、`basedpyright`、
`pytest backend/tests`、前端 `npm run format:check|test|build`。
