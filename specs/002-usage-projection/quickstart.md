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

预期：新增用例全绿；**既有失败集合不得扩大**（main 起点 13 failed / 160 passed / 21 skipped，分支终态 13 failed / 183 passed / 21 skipped，失败清单逐条一致，均为数据源策略未配置的既有问题）。

| 成功标准 | 验证方式 |
|---|---|
| SC-1 默认行为不变 | 不开投影设置跑全量既有用例原样通过；抽断 Model.requests 与现行为逐条一致 |
| SC-2 usage 缺失容忍 | fixtures 构造无 usage 的 completion，聚合输出 None 非 0、不报错 |
| SC-3 四不变量 | test_projection.py：配对完整 / 存活集合 / 占位编号清单 / 畸形输入原样返回 |
| SC-4 跨轮引用回归 | 恢复检查点后引用历史轮编号不触发 disputed；无来源编号仍拦截 |
| SC-5 并行隔离 | Send 并行下断言各 worker 的 model.request 互不污染、共享对象不受投影影响 |
| SC-6 质量门禁 | make quality 全过 |

## 手工冒烟（2026-11-14 真机已执行，记录与推荐配置）

真实模型（火山 coding 端点 + deepseek-v4-flash-ga）六轮冒烟，compose 栈 + `docker compose run -d --no-deps --service-ports backend`：

1. **投影关闭基线**：六轮全部携带 usage 契约；events 侧 prompt_tokens 之和与 API 返回逐轮一致（511/1468/7322/60219/32090/503）；排查轮单请求峰值 42,193 字符（大 SQL dump 反复携带）
2. **投影开启 30000/1200**：小轮次与关闭态一致（等价性验证）；排查轮峰值钳至 7,296；该轨迹下零占位触发（大结果恰在存活位，属预期）
3. **定向重演 30000/400**：占位触发 8/43 请求、峰值 22,817；模型诚实降级"投递详情未能获取"（防线正确但证据被压）——400 过激进

**试点推荐起点**：`FLOWPAY_CONTEXT_CHAR_BUDGET=30000`、`FLOWPAY_CONTEXT_TOOL_RESULT_BUDGET=1600`，观察回答质量后逐步收紧；配套驱动脚本 `scripts/smoke_002_usage.py`（usage 契约断言，容器内跑）与 `scripts/analyze_smoke.py`（体积/对账分析，参数：事件库目录 [session id]）。前端 UI 渲染用量属范围外（契约层已验证）。

## 已知环境注意

- **基线口径**：容器 main 起点 `13 failed / 160 passed / 21 skipped`、分支终态 `13 failed / 183 passed / 21 skipped`（--env-file .env）；验收时失败集合不得扩大
- **ruff EXE002**：Windows 挂载进容器后文件全带执行位，以宿主机 ruff 为准
- **langgraph 对齐**：pyproject/uv.lock 的版本约束改动是分支既有状态，全量测试依赖它，但不属于本特性验收范围
- 零依赖新增：不需要触碰 uv.lock 的依赖面（本特性自身的改动）
