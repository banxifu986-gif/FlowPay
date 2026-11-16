# 需求清单核对：004

- [x] 记忆只存排查结论（不做 s09 四类全谱）——Ban 2026-11-15 确认
- [x] 语义兜底用 SQLite+内存余弦，不用 pgvector——Ban 确认（偏离原表述，理由见 research.md）
- [x] seen 不淘汰，封顶查询键——Ban 确认
- [x] actions 封顶 50 保留——Ban 确认
- [x] 全部新配置 opt-in 缺省关闭（FLOWPAY_MEMORY_* / FLOWPAY_CONTEXT_BRIEFING_CAP）
- [x] 缺省关闭时请求与现行为逐字一致（US2/US6 验收断言）
- [x] 容器基线失败集合不扩大、零断言修改（13F/238P/3S @2026-11-15）
- [x] core 不依赖 workflows；workflows 禁 import sqlite3/psycopg（test_boundaries 守护）
- [x] 已知结构不退回 dict[str,Any]；中文注释解释约束原因（AGENTS.md）
- [x] 提交经 Ban 确认，不带署名
