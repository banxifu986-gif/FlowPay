# Quickstart: LangGraph 多 Agent 编排验收指南

Date: 2026-11-12。按 spec 成功标准逐条验证。

## 前置

1. Docker Desktop 运行中；`docker compose up -d postgres ollama` 已启动，`ollama-init` 已把 `qwen3-embedding:0.6b` 拉入 `flowpay_flowpay_ollama` 卷
2. 分支 `feat/langgraph-s5` 已创建，backend 已 `uv add "langgraph>=0.4,<0.6"`
3. `.env` 存在（含数据集路径与模型密钥，本指南不读取其内容）

## 自动化验收

```bash
# 全量回归（含 manual-loop 改名后回归 + multi-agent 新用例）
make test

# 质量门禁（ruff + basedpyright + pytest + 前端构建）
make quality
```

预期：全绿；全部 multi-agent 用例集中在 `test_multi_agent.py`（含并行调查与跨轮用例），覆盖下列映射。

| 成功标准 | 验证方式 |
|---|---|
| SC-1 规则短路零分类调用 | 测试消息带业务编号，断言 Model.requests 中无 set_intent 调用 |
| SC-2 闲聊零工具调用 | 断言该轮请求不带 tools 字段 |
| SC-3 并行不慢于串行 | max_concurrency=1 与默认并行的轮内耗时对照（SlowSearch 固定延时） |
| SC-4 引用可核查 | 命中片段随 `TurnResult.evidence` 返回（测试断言 evidence 含片段编号）；回答里的业务记录编号由 disputed 机制核对，文档引用编号协议层不改写正文、由界面据证据清单标注（答案契约） |
| SC-5 意图切换条件保留 | 排查→闲聊→排查序列，断言 case 键不变、无重复索要 |
| SC-6 查询去重 | 相同 tool:target 二次发起被 claim 拒绝 |
| SC-7 既有测试全绿 | make test |

## 手工冒烟（Phase H，实际执行口径）

Windows 宿主机直跑不可用（`core/event_log/runtime.py` 顶层 import fcntl），实际以 backend-dev 镜像容器冒烟：

- 环境准备（一次性）：`database.py setup` 建知识库角色；`scripts/knowledge.py build` 构建知识集合（CPU 嵌入约 100 分钟，容器内跑、用独立 FLOWPAY_DATA_DIR 避免与后端锁冲突）
- 后端：backend-dev 镜像挂载 backend/scripts/var/dataset，接入 `flowpay_default` 网络，env 与 compose backend 服务等价；knowledge DSN 口令默认 `flowpay_local`
- 真实模型走火山 coding plan 专属端点：`FLOWPAY_MODEL_BASE_URL=https://ark.cn-beijing.volces.com/api/coding/v3`（必须带 /v3）+ `deepseek-v4-flash-ga-260731`；同一 key 在普通 `/api/v3` 端点会报 ModelNotOpen（按量开通计费与套餐分离）
- 驱动脚本：`var/smoke_multi_agent.py`（六轮消息，覆盖下方场景并断言事件流）

冒烟脚本（同一会话内依次发送）：

1. "你好，你能做什么？" → 期待寒暄+能力介绍；trace 中无业务查询
2. "有笔支付成功了但一直没收到通知，帮我看看" → 期待追问缺项（waiting_user，前端出现追问）
3. "生产环境 acct_bg_001，支付编号 pay_bg_001_0003" → 期待并行调查后给出结论；引用的记录编号可点开核对
4. "谢谢" → 简短回应；随后继续问支付的事 → 期待不重复索要已给条件
5. "Webhook 重试策略是什么？" → 期待文档问答，回答带文档引用

预期：事件流（var/live 下 SQLite）中可见 route/set_intent trace、worker 并行记录、findings 摘要。

## 已知环境注意

- 嵌入模型端口必须 11434（向量空间校验）；compose 的 ollama 服务不发布宿主机端口，与宿主机已有 Ollama 不冲突
- 旧 s4 会话提交会收到 Conflict 提示，属预期行为（FR-12），引导新建会话即可
- 数据集未配置时 dataset 相关用例自动跳过（make test 已处理）
- **Windows 宿主机跑不了全量测试**：`core/event_log/runtime.py` 顶层 `import fcntl`（Unix-only），
  含 cli/http 的测试模块收集即失败。权威口径是在 Linux 容器里跑：
  `MSYS_NO_PATHCONV=1 docker run --rm --env-file .env -v "$(pwd)/backend:/app/backend" -v "$(pwd)/scripts:/app/scripts" -w /app flowpay-backend-dev python -m pytest backend/tests -q`
  （Git Bash 需 MSYS_NO_PATHCONV=1 防 `-w /app` 被路径转换）
- **改造基线**：容器全量 13 failed / 144 passed / 17 skipped（--env-file .env）；失败均为数据源策略
  未配置引发的既有问题，非本次改造引入。验收时失败集合不得扩大
- Windows 挂载进容器后文件全带执行位，ruff 会误报 EXE002——以宿主机 ruff 或真机 Linux 为准
