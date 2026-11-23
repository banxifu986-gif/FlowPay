# Quickstart: 008 推理能力验证与检索收尾

## 前置

- `.env`：OpenAI 官方（`FLOWPAY_MODEL_BASE_URL=https://api.openai.com/v1`、
  `FLOWPAY_MODEL_NAME=gpt-6-luna`、key 已配置）；代理 `HTTPS_PROXY/HTTP_PROXY=http://127.0.0.1:7897`
  + `NO_PROXY=127.0.0.1,localhost`。
- 嵌入：SiliconFlow bge-m3 已配置（`FLOWPAY_EMBEDDING_*`），知识集合 `flowpay_docs_v1_bge_m3` 就绪。
- 测试基线：513 passed / 5 skipped（本特性新增用例只增不减，失败集合必须为空）。
- 本机原生 venv 不能跑 pytest（fcntl）；脚本类命令可用宿主 venv。

## B1 专项测试

```bash
docker compose run --rm --no-deps backend python -m pytest \
  backend/tests/core/test_responses_adapter.py \
  backend/tests/core/test_responses_stream.py -q
# 全量：
docker compose run --rm --no-deps backend python -m pytest backend/tests -q
```

覆盖点：input 翻译（tool_calls/推理项回插）、聚合器事件类型白名单、usage 映射、
protocol 分流、保留字校验、reasoning_effort 白名单。

## B2 冒烟（6 题）

```bash
FLOWPAY_MODEL_PROTOCOL=responses \
FLOWPAY_MODEL_REQUEST_OPTIONS='{"reasoning_effort": "medium"}' \
docker compose run --rm --no-deps backend python scripts/benchmark/run.py \
  --stages multi-agent --per-type 1 \
  --governance --hybrid --config backend/configs/local.toml
```

验收：run.failed=0；意图/拆解/澄清链路正常（事件抽查）；记录单题耗时与 token，
外推 96 题总量并报告给 Ban。实际用量超外推 2 倍停手。

> 勘误（2026-11-23）：`FLOWPAY_MODEL_REQUEST_OPTIONS` 走 JSON 解析（`loader.parse_json`），TOML 写法（`reasoning_effort = "medium"`）会直接 JSONDecodeError——本文件两处已改为 JSON 写法；同时环境变量需经 `docker compose run -e VAR` 显式透传进容器。

## B3 推理臂 96 题

```bash
FLOWPAY_MODEL_PROTOCOL=responses \
FLOWPAY_MODEL_REQUEST_OPTIONS='{"reasoning_effort": "medium"}' \
docker compose run --rm --no-deps backend python scripts/benchmark/run.py \
  --stages multi-agent --per-type 16 \
  --governance --hybrid --config backend/configs/local.toml
```

对照基线：FULL300 同 96 题子集（`var/benchmark/20261121-full300-multi-agent.jsonl` 前 96 行）。
跑完生成：

```bash
backend/.venv/Scripts/python.exe scripts/benchmark/clarification.py \
  --arm R96=<新目录> --output var/benchmark/<日期>-clarification-r96.md
backend/.venv/Scripts/python.exe scripts/benchmark/attribution.py \
  --arm R96=<新目录> --arm BASE=var/benchmark/20261121-full300-report 目录 --output var/benchmark/<日期>-attribution-r96.md
```

机制门（SC-1）：覆盖 ≥90%、重复问 0、等待轮带结论 100%——任一不达标先排查再停手。
方向性目标（SC-2）：总 ≥23/96、多轮 ≥17/64。逐题对照产出翻盘/回退清单（SC-4）。

## B4 收尾包

```bash
# 默认开关切换后抽查：默认配置检索结果应与 bge-m3 基线纯向量口径一致
docker compose run --rm --no-deps backend python scripts/benchmark/retrieval.py \
  --per-type 16 --limit 20 --output var/benchmark/<日期>-retrieval-default-off.md
```

`knowledge_hybrid = false` 写入 local.toml（注释引两份全量对照报告）；
`dataset-gap-kb-api-017.md` 落 specs/008（缺失编号、引用题目清单、建议知识域、验证步骤）。

## 质量门禁

```bash
docker compose run --rm --no-deps backend python -m pytest backend/tests -q
ruff check backend/src backend/tests scripts && ruff format --check 同范围
basedpyright backend/src backend/tests scripts
```

## 回滚

推理路径出问题回滚 = `local.toml` 不设 `FLOWPAY_MODEL_PROTOCOL`（或设 chat_completions），
一行配置回到现状；混合检索回滚 = 改回 true。
