# 任务：004

批次对齐 plan.md；每批容器 pytest 失败集合不超基线（13F/238P/3S @2026-11-15）+ make quality。

## P0

- [x] T001 基线核对：容器 pytest 13F/238P/3S 逐条记录（2026-11-15 实测完成）
- [x] T002 core/memory/contracts.py：MemoryPolicy/MemoryNote/RecalledNote/MemoryRecall
- [x] T003 core/memory/store.py：MemoryStore（同键替换/最旧淘汰/单条截断，单事务）+ tests/memory/test_store.py
- [x] T004 core/memory/extract.py：extract_note 纯函数 + tests/memory/test_extract.py
- [x] T005 core/memory/recall.py：TracedMemoryRecall 键匹配 + 事件 + tests/memory/test_recall.py
- [x] T006 settings.py 三配置 + bootstrap.py 装配 + router.py 签名 + resources.py 生命周期
- [x] T007 manager.py 收尾抽取接线（_maybe_extract_memory + 事件）
- [x] T008 manual_loop briefing 记忆段注入 + multi_agent case_note 注入 + tests/workflows/test_memory_injection.py
- [x] T009 P0 回归：容器 13F/263P/3S——失败集合与基线逐条一致，零断言修改

## P1

- [x] T010 recall.py 语义兜底（嵌入查询 + 内存余弦 + 阈值；semantic_limit=0 零嵌入调用）；口径修订：语义兜底依赖 embedding 由 RAG 前置校验保证 + recall 层降级兜底（bootstrap 死比较已移除，见 spec.md US4）
- [x] T011 openai_chat.py ModelHTTPError + turn.py reactive compact + tests/workflows/test_reactive_compact.py（7 分支）
- [x] T012 P1 回归：失败集合不超基线，零断言修改

## P2

- [x] T013 ContextPolicy.briefing_cap + condense_briefings 接线 + tests/workflows/test_briefing_cap.py（6 用例含投影组合不变量）
- [x] T014 cap_query_keys（两 workflow 接线）+ ACTIONS_CAP + tests/workflows/test_query_keys_cap.py；DISPUTE 零误拦由既有回归钉住（seen 不动）
- [x] T015 P2 回归：容器 13F/281P/3S——失败集合与基线逐条一致，零断言修改

## Polish

- [x] T016 scripts/smoke_004_memory.py 四阶段真机跑通（2026-11-15：A 抽取 6 键带向量落库 / B 键召回 3/3 注入 / C 语义兜底命中 / D reactive 单测覆盖）；实施中发现并修复「写入时嵌入」缺口（抽取落库生成向量，见 quickstart 注意事项）
- [x] T017 003 spec 范围外两项加注「004 已立项」
- [x] T018 quickstart 校准数据回填（阈值 0.35 维持、嵌入耗时 21-35s 实测等）；ruff + basedpyright 0 错 0 警，容器 13F/281P/3S 基线一致
