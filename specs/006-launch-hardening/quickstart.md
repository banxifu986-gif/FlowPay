# Quickstart: 006 网关加固与上线增强

## 前置

```bash
# 005 的登录门禁仍是前置：先生成账号并写入令牌密钥（不要提交 .env）
backend/.venv/bin/python scripts/accounts.py seed \
  --merchant-account acct_merchant_demo \
  --merchant-password '<商户口令>' \
  --internal-password '<内部口令>'
# 把脚本打印的 FLOWPAY_AUTH_TOKEN_SECRET 写入 .env
```

`backend/configs/local.toml` 已回填 003/004 smoke 校准值与 006 默认守卫值。
本机/容器覆盖仍走 `FLOWPAY_<字段>` 环境变量。

## 一键全栈冒烟（US6）

```bash
make up
# 后端与前端就绪后，容器内执行：
docker compose exec -T backend python scripts/smoke_006_launch.py \
  --username internal_demo --password "<内部口令>"
```

脚本依次验证：登录 → 创建会话 → 提交问题 → 读取 SSE 事件直至终态 → 提交 👍 反馈
→ 事件日志出现 `feedback.received`。反代位置只在 compose 里以注释预留，不实现。

## 用量守卫（US1）

```bash
# 默认：每商户 200 回合/UTC 日、并发 1、全局 5000 回合/UTC 日
# 覆盖示例（compose run 或 .env）：
FLOWPAY_QUOTA_MERCHANT_DAILY_TURNS=20 \
FLOWPAY_QUOTA_GLOBAL_DAILY_TURNS=100 \
FLOWPAY_QUOTA_MERCHANT_CONCURRENCY=1
```

超限接口返回 HTTP 429：`{"detail": "<友好文案>", "code": "quota_exceeded"}`。
内部账号另有 `FLOWPAY_QUOTA_INTERNAL_DAILY_TURNS`（默认 500），避免单账号吃满全局额度。

### 部署约束（已知取舍）

- 全局日上限与商户并发是**单进程**口径：配额预留与并发计数在管理器内存中，
  事件日志只提供跨进程的日计量；当前部署形态必须是**单 worker / 单写者**。
- 全局日上限在并发窗口内是 check-then-act 软限制，不是精确配额服务。
  多实例部署前需把配额预留落事件日志并加跨进程并发锁。

## 结构化错误与降级（US2）

失败分类映射见 data-model.md。重试过程在轨迹中记 `run.retry`，终态 `run.failed`
载荷含 `category` 与用户可见 `message`；完整异常只进服务端日志。

## 上下文管理默认启用（US3）

默认值（与 003/004 冒烟校准一致）已在 `local.toml`：

```toml
context_char_budget = 30000
context_tool_result_budget = 1600
context_compaction_trigger_chars = 30000
context_summary_char_budget = 4000
context_report_char_budget = 4096
context_briefing_cap = 6
memory_recall_char_budget = 2000
memory_semantic_limit = 2
memory_max_notes = 200
```

基准默认关闭新治理项以保持与 005 抽样同口径；需要对照时显式加 `--governance`，
运行输出会打印所用配置。

## 混合检索（US4）

```bash
# 知识库迁移会创建 pg_trgm 与 GIN 索引
make db-setup
```

生产配置 `knowledge_hybrid=true`。关键词路与向量路各取候选后 RRF 融合；证据字段
（collection/doc/chunk/sha256/locator）不变。离线对比数据记录在交付报告。

## 反馈闭环（US5）

前端每轮回答下可 👍/👎（可选附言）。事件 `feedback.received` append-only；
同一 `(会话, 轮次, 主体)` 重复提交幂等、不追加事件，不同主体互不阻挡；
运行中的轮次不接受反馈。反馈不会进入会话消息或检查点，因此不污染后续轮次。

## 质量门禁

```bash
docker compose run --rm --no-deps backend python -m pytest backend/tests -q
make quality
make benchmark    # PER_TYPE=2 抽样，内部账号
# --governance 真跑时检索固定纯向量，报表按实际 settings 标注检索路径
# --hybrid 是唯一开启混合检索的基准开关
```
