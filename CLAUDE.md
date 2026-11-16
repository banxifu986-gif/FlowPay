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

## 项目编码约定

- 每个阶段使用 workflows/sN.py 独立文件，只写本阶段编排、提示词和业务决策；不继承或调用其他阶段的 run，只注册已实现阶段。
- workflows 通过类型明确的能力接口调用下层，不碰底层实现。
- workflows 不创建 SDK/数据库客户端，不实现 HTTP/SSE/SQL/文件读取、追踪落库、模型原生协议解析或样例工具。不得把整个业务循环藏进下层实现。
- 通用能力及其实现放 core；发布文档导入、模拟服务与验收放 scripts；具体对象装配放 flowpay/bootstrap.py，阶段注册与选择放 flowpay/router.py。core 不依赖 workflows、router、bootstrap 或 scripts。
- 公共方法声明清楚输入和返回类型，动作使用有意义的动词。已知结构不要退回 dict[str, Any]；供应商扩展和阶段状态可保留 JSON 边界。
- __init__.py 仅作公共导出。先改善名称和结构，再写中文注释解释约束、原因、事务及取消行为；不以注释数量或短文件作为质量标准。
- 前端组件声明实际所需 props；事件解释集中在投影层，订阅、运行操作与本地草稿分别有明确所有者。
