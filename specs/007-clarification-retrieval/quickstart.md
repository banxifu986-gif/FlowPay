# Quickstart: 007 多轮澄清覆盖与文档召回

## 前置

- `.env` 已切到 DeepSeek 官方：`FLOWPAY_MODEL_NAME=deepseek-flash`，
  基址 `https://api.deepseek.com/v1`（验收轮 2026-11-19 起的实际口径）。
- 知识库与嵌入服务可用（`make up` 或 `make db-up` + `make db-setup`）。
- 数据集根 `flowpay-dataset` 已配置（`FLOWPAY_DATASET_ROOT`）。
- 基线臂：`var/benchmark/20261117-071251-67e7d4`（C 臂，96 题，23/96、32/96、多轮 14/64）。
- 正式验收臂：`var/benchmark/20261119-060338-19ee17`（43/96、54/96、多轮 24/64、单轮 19/32）。
  全部命令与结果的结论见同目录 `delivery-report.md`。

## 专项测试

容器内跑（本机原生 pytest 因 `fcntl` 不可用）：

```bash
docker compose run --rm --no-deps backend python -m pytest backend/tests -q
# 只跑本特性相关：
docker compose run --rm --no-deps backend python -m pytest \
  backend/tests/workflows/test_clarification.py \
  backend/tests/core/test_bound_scope_tools.py \
  backend/tests/scripts/test_benchmark_clarification.py \
  backend/tests/scripts/test_benchmark_retrieval.py -q
# 当前基线：505 passed, 5 skipped，失败集合为空
```

覆盖点：状态序列化与恢复（含 asked_conditions）、工具去重/合并、plan 抽取、
synthesize 决策与回退、绑定账户 schema、最终轮信号、采纳轮口径的覆盖率与
重复问计算、actual 口径构造。

## 手工对话验证（US1–US4）

内部账号：

1. 发“有笔支付成功了但没收到通知，帮我看看” → 回答应先给已查到的文档结论与引用，
   再一次索要账户、环境、编号、目标/现象/时间/版本中尚缺项，状态等待用户。
2. 补齐账户与环境 → 不再索要这两项，继续业务查询；已问过的类别不会换字段重复问。
3. 对“发生时间”回复“查不到” → 后续不再追问时间，回答标注未能确认。
4. 商户账号（005 登录）发起排查 → 追问中不出现账户编号；内部账号行为不变。

## 离线检索诊断（US5）

```bash
docker compose run --rm --no-deps backend python scripts/benchmark/retrieval.py \
  --per-type 16 --limit 20 \
  --answers-arm var/benchmark/<新版臂目录> \
  --output var/benchmark/<日期>-retrieval-actual.md
```

四种查询口径：initial（首条消息）、observed（首条消息+臂内用户回复，需
`--observed-arm`）、actual（首条消息+最终检查点 `clarification.answers`，
SC-5 验收口径）、full（全部可披露事实，仅上界）；× 两条检索路径
（vector/hybrid），输出 recall@k、口径差值、结论与不可召回清单。脚本只调用
嵌入与向量库，不调用聊天模型。

验收判据（SC-5）：给了 `--answers-arm` 时按 actual-initial 的 recall@10 ≥ +10pp
判定；actual <10pp 时停下讨论，不硬凑。

实际跑通的产物：`var/benchmark/20261119-retrieval-actual.md`
（actual 74.6%，+12.6pp 达标；full 上界 77.9%；KB-API-017 不可召回单列）。

## 端到端基准与机制门（US1–US6 验收）

```bash
docker compose run --rm --no-deps backend python scripts/benchmark/run.py \
  --stages multi-agent --per-type 16 \
  --governance --hybrid --config backend/configs/local.toml
```

保持与 C 臂相同配置；模型三项从 `.env` 注入（容器自动带上）。跑完记下输出目录，
再生成机制报告与检索核对：

```bash
docker compose run --rm --no-deps backend python scripts/benchmark/clarification.py \
  --arm NEW=var/benchmark/<新版目录> \
  --output var/benchmark/<日期>-clarification-final.md

docker compose run --rm --no-deps backend python scripts/benchmark/retrieval.py \
  --answers-arm var/benchmark/<新版目录> \
  --output var/benchmark/<日期>-retrieval-actual.md
```

机制门（SC-1/SC-3/SC-2 基准侧）：整题覆盖 ≥90%、重复问 0、等待轮带结论 100%。
端到端门槛（SC-4）：总 ≥23、多轮 ≥17、证据 ≥32、单轮 ≥9。注意单次 96 题噪声：
两次 96 题运行总通过差 4 题（47→43）属模型波动；不要求统计显著，但总通过不得低于 23。

实际跑通的产物：机制报告 `var/benchmark/20261119-clarification-final.md`
（50/50=100%、重复问 0、74/74 带结论）；旧臂基线核对报告
`var/benchmark/20261118-clarification-baseline.md`（A/C 两臂 101 等待轮等）。

如需失败题归因：`scripts/benchmark/attribution.py --arm A=<目录> --arm C=<目录>`
（口径说明见脚本 docstring 与 tasks.md T031）。

## 基线与模型复核

新版正式跑之前，先用同模型、同 96 题、同配置跑一次“基线复核”。若总分与
`var/benchmark/20261117-071251-67e7d4` 相差超过 ±3 题，停手讨论模型是否真的一致，
不把差异归因于 007 的代码。（007 实际执行中首轮与 C 臂差 +24 题，原因是该轮即
新版端到端而非模型漂移，已与 T035 合并，记录见 tasks.md T034。）

## 质量门禁

```bash
make quality          # ruff + basedpyright + pytest + 前端（宿主 venv 环境）
# 容器内 pytest：
docker compose run --rm --no-deps backend python -m pytest backend/tests -q
```

Windows 本机把文中 `backend/.venv/bin/python` 换成 `backend/.venv/Scripts/python.exe`；
但不要用本机 venv 跑 pytest：事件日志实现依赖 `fcntl`，收集阶段会失败。脚本类命令
（benchmark、retrieval、clarification）在 Windows 上可用 venv 直接跑（需先
`set -a; . ./.env; set +a` 注入数据集环境），或统一用容器。

容器内直接跑 basedpyright 会因镜像里 `backend/.venv` 缺 Linux 布局误报大量告警
（`pyrightconfig.json` 的 venvPath 解析回退）；basedpyright 门禁以宿主 venv 口径为准
（200 files，0 errors / 0 warnings）。
