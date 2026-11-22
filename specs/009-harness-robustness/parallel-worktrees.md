# 并行开发计划：009 多 worktree 开发

状态：修订版 v2（2026-11-23 外部审查【修改后批准】的四项修正已回写：D1 venv 方案、D2 端口/DB_HOST 纪律、D3 切出点、E3 费控执行权；实施前需 Ban 批准 T000 提交门）

## 一、依赖事实（来自 tasks.md 与 plan.md 分层接线）

| 事实 | 对并行的后果 |
| --- | --- |
| T005（`state.py` SharedTurn 扩展：tool_outages / task_progress / task_tool_calls）是 US1 与 US2 的共同地基 | 单一 owner 先落，禁止两个 worktree 各改一份 `state.py` |
| `investigation.py` 被 5 处触碰：US1（T006 工具包装、T007 resolve_response 降级）、US2（T011 turn.progress）、US3（T017 校验接入）、US4（T020 CapabilityMap）、US5（T023 query_with_findings） | 合并冲突热点；副轨只加新函数/新表，不动脊柱函数体 |
| `manager.py` 被 T004（US1 deterministic 短路）与 T013（US2 resume 通道）触碰 | 同文件两处，脊柱内串行 |
| 语义耦合：US2 依赖 US1 的 outage/progress 框架；FR-7 把预算收束并入 US3 的"未能核实"口径 | 脊柱（US1）先合入，副轨 rebase 后才能合 |
| T026 冒烟 / T027 全臂：费控门 + 单次运行（~2.5h） | 只在主 worktree 串行执行，不并行 |

文件级不相交的并行集：US3 主体（`citation_check.py` 新文件 + `scripts/benchmark` 报表扩展）与主线几乎零交集；US4/US5 各动 `investigation.py` 的不同函数区域。

## 二、推荐拓扑（1 主 + 2 副，期望收益 ~1.5x）

```text
主 worktree（integration 分支 feature/009-harness-robustness）：
  US1 → US2 串行脊柱（T003–T015；共享 SharedTurn/manager 框架，拆开互相等）
副 worktree ①：US3（T016–T019），切出点必须含 T005+T007（T017 改的就是 T007 改过的 resolve_response）
副 worktree ②：US4 + US5（T020–T025），切出点含 T005 即可
合并顺序：脊柱合入 → 副轨 rebase → T026 冒烟 → T027 全臂 → T028–T030 收尾
费控与验收（T026/T027）由主线会话统一执行，并行会话不得自行放行或跑基准
```

## 三、前置条件（六条，缺一并行即失败）

1. **先提交 009 文档套件（tasks.md T000）**：`specs/009-harness-robustness/` + `.gitignore` + `.specify/feature.json` 提交入库——worktree 不共享未跟踪文件，副轨看不到 spec/tasks。
2. **每 worktree 一份 `.env`**（gitignored 不共享）：复制主仓 `.env`，`FLOWPAY_DATASET_ROOT` 可指向同一数据集解包目录。
3. **`docs/wiki` 在副轨不可见**（主仓 `.gitignore` 的副作用）：需要 wiki 时用 Windows junction（`mklink /J <worktree>\docs\wiki D:\coding\project\Flow\docs\wiki`）挂入副轨**只读使用**；摄入仍只在主 worktree，双处摄入=分叉。
4. **测试解释器（审查 D1 修订：废弃 VENV 共享）**：各 worktree **各自 `uv sync`** 建自己的 venv（Windows 下 Scripts 布局）。理由：本机 `backend/.venv` 实际为 Scripts 布局，而 `Makefile:12`（`VENV := backend/.venv/bin/python`）与 `scripts/check_quality.py:19` 硬编码 POSIX `bin/python`，共享方案会撞上双布局解析与 `pyrightconfig.json` `venvPath`（相对各 worktree 解析）在副 worktree 无 venv 的未定义行为（007 交付报告 6.3 有解析回退误报先例）。**待实测确认**：本机 `make quality` 历史上实际跑在哪个环境（WSL `.venv-wsl` / 容器 / Git-Bash）——影响 T029 门禁的执行方式，不影响并行结构本身。
5. **容器纪律（审查 D2 补充）**：`compose.yaml` host 端口固定（postgres 55432 / backend 8000 / frontend 5173），且 `Makefile:23` `DB_HOST := 127.0.0.1:55432` 硬编码——副 worktree 即便用 `compose.override.yaml` 改端口，其宿主机直跑路径仍会连到主 worktree 的 postgres。纪律：**副 worktree 禁止 `make up`/起全栈容器**，测试走宿主 pytest（各自 venv）或经主 worktree 容器串行；基准臂只在主 worktree（ollama 卷、dataset-init 双份实例问题随之消除）。
6. **合并纪律**：副轨不动 `resolve_response` / `dispatch` / `SharedTurn` 既有函数体；各自过 `make quality`（各自 venv），合并后主 worktree 终跑一次（SC-8）。

## 四、风险

- `investigation.py` rebase 成本：合并顺序错会反复解冲突；纪律优先于速度。
- API 限流与费用：多 agent 会话并行开发可行，但基准臂绝不并行（限流 + 费用翻倍）；并行开发不增加费用（全臂仅一次）。
- `.specify/feature.json` 为共享文件：多会话若同时跑 speckit 命令会互相覆盖指向；本计划下 spec 阶段已收口，实施阶段无此问题。
- Windows junction 的只读纪律需人工遵守。
- **门禁执行环境待实测**：`Makefile`/`check_quality.py` 的 POSIX `bin/python` 硬编码与本机 `backend/.venv`（Scripts 布局）矛盾——T029 `make quality` 在本机的实际执行形态（WSL/容器/Git-Bash）需开工前确认（审查 E1），否则 SC-8 无法落地。

## 五、明确不可并行项

T001 基线快照、T005 地基、T026/T027 验收臂、T029/T030 收尾门禁。
