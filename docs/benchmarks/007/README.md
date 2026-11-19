# 007 基准归档索引

轻量归档：每臂逐题明细（multi-agent.jsonl）+ 对照报表（report.md）+ 过程分析报告。
完整事件库（sessions.sqlite，每臂 450–910MB）不入库，已按交付报告方案留待外部备份；
数据源目录在 `var/benchmark/`（gitignore），如需复查逐轮事件按 delivery-report.md 第 5 节的目录名定位。

## 臂

| 文件 | 说明 |
| --- | --- |
| `baseline-A-*` | 20261117-071249-49fd18，006 基线（治理关、纯向量），18/96 通过、35/96 证据 |
| `baseline-C-*` | 20261117-071251-67e7d4，006 生产默认（治理开、混合），23/96、32/96 —— 007 的对比基线 |
| `first-round-*` | 20261118-134203-c49ac0，007 机制首轮（T034/T035 合并轮），47/96、53/96；机制门重复问 37 处未达标触发修复重跑 |
| `final-*` | 20261119-060338-19ee17，007 正式验收臂，43/96、54/96；机制门 100%/0/100% 全达标 |

## 过程报告（时间序）

| 文件 | 说明 |
| --- | --- |
| `20261117-attribution.md` | 96 题双跑失败归因（006 收尾） |
| `20261117-retrieval-diagnosis.md` | 检索诊断：initial 62.1% / full 上界 77.9% @10，判定查询缺信息 |
| `20261118-retrieval-observed-fixed.md` | observed 上界复测 +2.6pp（未达标；初版报告因 collect 误用 full 已作废删除） |
| `20261118-clarification-baseline.md` | 旧臂机制指标基线（A/C 各 101 等待轮；多轮发问 A 60 / C 58，全覆盖 0） |
| `20261119-clarification-final.md` | 正式轮机制门：整题覆盖 50/50、重复问 0、等待轮带结论 74/74 |
| `20261119-retrieval-actual.md` | actual 口径（initial + 检查点 answers）recall@10 +12.6pp，SC-5 达标 |

结论与口径演进见 `specs/007-clarification-retrieval/delivery-report.md` 第 3/4 节。
