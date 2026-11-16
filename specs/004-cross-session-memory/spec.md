# 规格：004 跨会话记忆 + 上下文管理硬化

状态：已批准（2026-11-15，随分支 feature/004-cross-session-memory 实施）
前置：specs/003-context-management 已收尾；003 范围外「真·跨会话记忆（前置工程：排查结论抽取）」由本规格立项落实。
参考设计：D:\coding\learn-claude-code s08（上下文压缩）/ s09（记忆四子系统）。

## 目标

1. 跨会话排查记忆：会话收尾把排查结论确定性抽取为记忆，新会话遇相关 case/记录编号时召回注入简报；键精确匹配优先，语义相似度兜底。
2. 上下文硬化三项：manual-loop briefing 封顶、API 超限补救（reactive compact）、查询键封顶。
3. 默认行为逐字不变：全部新能力 opt-in，缺省关闭。

## 用户故事

- **US1（P0）记忆存储与确定性抽取**：memory.sqlite 单表 + MemoryStore（同键全等替换、总量最旧淘汰、单条截断）；SessionManager 收尾纯函数抽取（零 LLM）。验收：completed 且有结论才存；waiting_user/零进展发 extract_skipped；缺省关闭零文件零事件。
- **US2（P0）结构化键召回与注入**：键精确召回，段落并入当轮 briefing system 消息（manual-loop briefing 拼接处 / multi-agent case_note），带「与本轮冲突时以本轮为准」话术；超预算 clip_text；召回失败不阻断。
- **US3（P0）默认行为完全不变**：容器基线逐条一致（2026-11-15 实测 13F/238P/3S：11×test_agent_loop manual-loop 参数组 + 2×test_multi_agent），零断言修改；真机冒烟脚本两阶段校验。
- **US4（P1）语义兜底召回**：键未命中时嵌入查询 + 内存余弦 top-k，阈值过滤；语义兜底依赖 embedding 配置——RAG 前置校验已保证其存在，recall 层仍有独立兜底（缺配置时退化为键匹配）；=0 时零嵌入调用。
- **US5（P1）reactive compact**：ModelHTTPError(400+超限关键词) → 绕过触发线强制压缩 → 重试一次；压缩失败跳过补救并上抛原异常；重试再失败上抛重试自身的异常（不再二次补救）；仅 remember=True 且有 policy 时启用；context.reactive_compacted 事件。
- **US6（P2）briefing 封顶**：condense_briefings 写路径合并（ContextPolicy.briefing_cap>0 才启用）；合并条目带「已知条件（历史简报合并）」前缀可继续参与合并；与压缩/投影正交。
- **US7（P2）查询键封顶 + actions 治理**：cap_query_keys 保当前 findings/actions 引用键全保留、其余按序保尾部淘汰（QUERY_KEYS_CAP=200）；seen 不淘汰（DISPUTE 防线不削弱）；manual-loop checkpoint actions 封顶 50 保最新。
- **US8（Polish）校准回填**：quickstart 真机数据回填；003 spec 范围外两项加注。

## 范围外

- s09 全套四类记忆（用户偏好/反馈/项目事实/参考）；LLM 记忆蒸馏
- token 级预算/分词器；模型主动压缩工具；recall 证据回查工具（003 已推迟）
- 事件日志增长治理、ContextPolicy 启用粒度、Postgres EventStore、Windows 运行锁适配（基建另立）
- 前端治理可视化；记忆与 delete_sessions 联动删除
- pgvector 存记忆（集合就绪后不可变；量级 ≤200 用 SQLite JSON 列 + 内存余弦即可）

## 验收标准（总）

- `docker compose run --rm --no-deps backend python -m pytest backend/tests -q`：失败集合不得超出基线 13 条，零断言修改。
- `make quality` 全绿（ruff + basedpyright + pytest + 前端构建；Windows 宿主机 fcntl 缺失的 10 个收集错误为环境口径，以容器为准）。
- 真机冒烟（scripts/smoke_004_memory.py）：关闭键逐字一致；开启键跨会话召回生效；校准值回填 quickstart。

## Clarifications（已与 Ban 确认）

1. 记忆只存排查结论（不做四类全谱）。
2. 语义兜底存储为 SQLite + 内存余弦（偏离「复用 pgvector 基建」原表述，理由：集合不可变约束 + 测试口径 + 量级）。
3. seen 不淘汰，封顶对象为查询键集合。
4. manual-loop checkpoint actions 封顶 50 保留（不停止保存）。
5. 交付走 spec-kit 工件 + feature 分支；全部 opt-in 缺省关闭。
