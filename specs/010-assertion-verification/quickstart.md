# 快速验证：010 逐断言校验

本特性开发期全部走旁路/单测，不启动全栈容器（副树禁 `make up`，沿用 009 纪律）。验证分四层，全部在各自 worktree 的 venv 内执行（宿主 pytest 子集快跑；fcntl 容器-only 模块不在本特性改动面）。

## 1. 校验器能力单测（树A / 合并后主仓）

```bash
cd backend && uv sync
.venv/Scripts/python.exe -m pytest tests -k "verify" -q          # Windows 布局；POSIX 用 .venv/bin/python
```

覆盖点（对应 spec SC-3）：四档判定 fixture（含三类越界样例：适用对象扩大/"通常"→保证/"工作日"丢失）、ID 存在性（编造片段引用→insufficient）、句级替换子集校验（改写引入新数字→降级）、降级幂等（处置后文本再过校验器不二次改写）、fail-open（调用/解析失败→放行原文）。

## 2. 接线与开关回归（树B / 合并后主仓）

```bash
.venv/Scripts/python.exe -m pytest tests -k "assertion_gate or audit" -q
```

关键断言（可证伪形态）：开关关闭（默认）时作答路径零新增模型调用、零新增事件（SC-1）；开启时 `assertion.audit` 事件载荷含 verdicts/actions/fail_open，矛盾断言带 conflict 详情；预算耗尽跳过校验等效 fail-open。

## 3. 冒烟回放（合并后主仓，B3）

```bash
set -a; . ./.env; set +a
backend/.venv/Scripts/python.exe scripts/benchmark/assertion_audit.py \
  --arm var/benchmark/20261122-200707-6010b8 \
  --questions FP-BM-SR-0001 FP-BM-MD-0022 FP-BM-MD-0062 FP-BM-SR-0061 FP-BM-MD-0030 FP-BM-MD-0042 \
              FP-BM-MD-0018 FP-BM-SR-0021 FP-BM-MS-0008 FP-BM-SR-0005 FP-BM-SR-0009 FP-BM-SR-0013 \
  --report specs/010-assertion-verification/smoke \
  --verifier scripts.benchmark.assertion_audit:core_verifier
```

前置：009 验收臂事件库在本机（`var/benchmark/20261122-200707-6010b8`）；API Key 走环境变量（`.env`，不进配置快照与日志）。题单（审查 P2-7：`--sample 12` 是"每题型前 12 道"=72 题，不是 12 题——冒烟口径用显式题单）：没召回嫌疑（evidence_miss）×3、表述越界嫌疑（conclusion_wrong）×3、绑错嫌疑×3、通过题×3，题号来自 009 attribution 报告。期望产出：12 题逐断言判定 JSONL + 三类断点（没召回/绑错/表述越界）至少各 1 例 + 矛盾档 + 通过题误伤样例（SC-4）。费用 ≈ 24 次校验调用（12 题 × 2），属冒烟量级。

## 4. 全量回放与开启态评测臂（B4，Ban 批准后）

```bash
# 全量 96 题回放（SC-5）：断点分布 + 误伤基线，≈192 次校验调用
.venv/Scripts/python.exe scripts/benchmark/assertion_audit.py --arm var/benchmark/20261122-200707-6010b8 --all

# 开启态评测臂（SC-6）：local.toml 设 assertion_audit_enabled=true 后
make benchmark
```

go/no-go 判据：回放误伤基线可控（通过题误判样例人工复核）才放行开启态臂；SC-6 通过线 = 总通过 ≥20/96、断言覆盖 >47、通过题转失败 ≤2。

## 质量门（每树各自，合并后主仓终跑）

```bash
make quality    # ruff + basedpyright + pytest + 前端构建
```

失败基线必须为空，不滚动（CLAUDE.md 测试约定）。
