# 快速验收：FlowPay 大众版 FAQ

> `freeze` / `verify` 可离线运行；首版 `build` 已在断网的本地模型和独立 pgvector 上验收。真实 Agent 评测和演示命令仍为交付目标。使用项目现有 Python 环境，所有工作在仓库根目录；发布物与运行状态写入新的 `var/faq/` 子目录。禁止在原 `flowpay-dataset/` 上执行构建。

## 1. 冻结并构建新套件

真实 `build` 需要独立 FAQ 写库和获准使用的嵌入环境。通过 `FLOWPAY_FAQ_KNOWLEDGE_BUILDER_DSN` 和 `FLOWPAY_EMBEDDING_*` 提供配置，见[数据集 README](../../flowpay-faq-dataset/README.md#独立摄入)。凭据不放在命令参数、公开发布物或报告中。

```bash
python -m scripts.faq freeze --source flowpay-faq-dataset \
  --output var/faq/releases/v1 --release-id flowpay-faq-v1
# 把上一条输出的固定清单摘要填入 FAQ_RELEASE_SHA256。
python -m scripts.faq verify --faq-root var/faq/releases/v1 --release-sha256 "$FAQ_RELEASE_SHA256"
python -m scripts.faq build --faq-root var/faq/releases/v1 --release-sha256 "$FAQ_RELEASE_SHA256"
```

预期：报告约 20 篇主题 FAQ、100 题及 40／60 拆分；只构建 FAQ collection，素材和运行视图可分别验证。对一份临时副本执行缺文件、错摘要和集合／快照混版检验，应拒绝且明确指出阶段。用原套件的现有回归命令验证默认行为无变化。

## 2. 跑评测

```bash
python -m scripts.benchmark.run --faq-root var/faq/releases/v1 \
  --release-sha256 "$FAQ_RELEASE_SHA256" --split dev --per-type 2 --stages multi-agent
python -m scripts.benchmark.run --faq-root var/faq/releases/v1 \
  --release-sha256 "$FAQ_RELEASE_SHA256" --split test --per-type 0 --stages multi-agent
```

预期：先在 dev 对照六类取证结果和评分夹具；不把金标传给助手。正式 test 在 dev 调试结束、运行参数固定后执行；结果含 60 道题的执行／评分与分型、难度报告。运行或评分异常要显式说明，不从分母移除。模型通过率不设阈值。只有合成数据能进入真实模型链路；如果所用模型服务是外部服务，运行前确认允许发送本次合成题面／取证，以及评测端用于语义评分的合成金标。

## 3. 本地聊天页冒烟

专用模拟账号和令牌密钥按现有认证方式由本机提供；启动前确认 FAQ 后端状态路径和端口未被原服务占用。只将运行视图交给后端：

```bash
flowpay --config backend/configs/local.toml --faq-root var/faq/releases/v1/runtime \
  --release-sha256 "$FAQ_RELEASE_SHA256"
FLOWPAY_API_PROXY=http://127.0.0.1:8011 npm --prefix frontend run dev -- --host 127.0.0.1 --port 5174
```

两个进程各自启动、验证后停止。打开 [本地 FAQ 聊天页](http://127.0.0.1:5174/)，完成：

1. 用付款人 A 问退款进度，不先给足条件；按助手追问补齐，查看记录编号和 FAQ 引用。
2. 在 A 会话询问属于 B 的付款编号，再询问一条不存在的编号：两者不可据此区分；事件也不得包含 B 的记录结果。用 B 自己的登录身份查询该付款，验证正向可用。
3. 不带 FAQ 参数运行原模式的既有冒烟；仍使用原知识库、记录和登录状态，不出现 FAQ 入口或评测资料。

判定口径见 [规格](spec.md) 和 [FAQ 套件契约](contracts/faq-suite.md)。
