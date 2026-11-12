# FlowPay

支付平台技术支持助手：多轮对话排查支付/事件/Webhook 问题，回答基于业务记录与知识库文档。

## 结构

- `backend/src/flowpay/core/` — 通用能力层（agent 循环、预算、执行契约、知识检索、数据源、LLM、事件日志）；不依赖业务层
- `backend/src/flowpay/workflows/` — 阶段编排；每个阶段一个包/文件
- `backend/src/flowpay/bootstrap.py` — 对象装配；`router.py` — 阶段注册与选择
- `scripts/` — 发布文档导入、模拟服务、验收与基准
- `specs/` — 特性需求沉淀（spec-kit）；当前特性见 `specs/001-langgraph-multi-agent/`

## 阶段命名

用描述性名字，不用教学序号：

- `manual-loop` — 单 Agent 手动编排调查循环（现有，代码 `workflows/manual_loop.py`）
- `multi-agent` — 意图路由 + 并行协作排查（默认，见 specs/001）

## 常用命令

```bash
make test                          # backend pytest（根目录）
make quality                       # ruff + basedpyright + pytest + 前端构建
make up                            # 全栈容器
make run STAGE=multi-agent         # 宿主机起后端
cd backend && uv run pytest        # 后端目录内直接跑测试
```

## 约定

项目编码约定见 `AGENTS.md`（分层边界、类型声明、注释要求）。
