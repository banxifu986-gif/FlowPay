# Quickstart: 模型用量聚合与上下文投影验收指南

Date: 2026-11-14。按 spec 成功标准逐条验证。

## 前置

1. 分支 `feat/002-usage-projection`（工作区携带 pyproject/uv.lock 的 langgraph 版本对齐改动——分支上的既有状态，非本特性产物，提交时与 Ban 确认归属）
2. 无新增依赖，无需拉镜像或重建向量
3. `.env` 存在（本指南不读取其内容）

## 自动化验收

权威口径在 Linux 容器（Windows 宿主机受 fcntl 限制无法全量收集）：

```bash
# 全量回归（既有 + 新增用例）
MSYS_NO_PATHCONV=1 docker run --rm --env-file .env -v "$(pwd)/backend:/app/backend" -v "$(pwd)/scripts:/app/scripts" -w /app flowpay-backend-dev python -m pytest backend/tests -q

# 质量门禁（ruff 以宿主机为准：Windows 挂载文件全带执行位，EXE002 误报）
make quality
```

预期：新增用例全绿；**既有失败集合不得扩大**（基线 13 failed / 144 passed / 17 skipped，均为数据源策略未配置的既有问题）。

| 成功标准 | 验证方式 |
|---|---|
| SC-1 默认行为不变 | 不开投影设置跑全量既有用例原样通过；抽断 Model.requests 与现行为逐条一致 |
| SC-2 usage 缺失容忍 | fixtures 构造无 usage 的 completion，聚合输出 None 非 0、不报错 |
| SC-3 四不变量 | test_projection.py：配对完整 / 存活集合 / 占位编号清单 / 畸形输入原样返回 |
| SC-4 跨轮引用回归 | 恢复检查点后引用历史轮编号不触发 disputed；无来源编号仍拦截 |
| SC-5 并行隔离 | Send 并行下断言各 worker 的 model.request 互不污染、共享对象不受投影影响 |
| SC-6 质量门禁 | make quality 全过 |

## 手工冒烟（可选，opt-in 后）

1. 开启投影配置，驱动一段长排查会话（多轮、SQL 结果大）
2. 检查 var/live 事件库：后续 `model.request` 的 messages 体积受控；早期 tool 消息为占位摘要且保留 record_ids
3. 前端回答旁可见 usage 数值；compatible 端点缺失 usage 时展示"未知"而非 0
4. 跨轮引用上一轮记录编号，回答不被 disputed 拦截；引用编造编号仍被拦截

## 已知环境注意

- **基线口径**：容器 `13 failed / 144 passed / 17 skipped`（--env-file .env）；验收时失败集合不得扩大
- **ruff EXE002**：Windows 挂载进容器后文件全带执行位，以宿主机 ruff 为准
- **langgraph 对齐**：pyproject/uv.lock 的版本约束改动是分支既有状态，全量测试依赖它，但不属于本特性验收范围
- 零依赖新增：不需要触碰 uv.lock 的依赖面（本特性自身的改动）
