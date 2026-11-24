# 接口与验收契约：检索链路优化（012）

## 1. KnowledgeClient.search_variants

```
async def search_variants(
    self, texts: Sequence[str], *, limit: int, filters: JsonObject | None
) -> list[Passage]
```

- **输入**：`texts[0]` 为原句（必须非空），其余为变体（0-3 条，已去重）。
- **行为**：一次批嵌入全部文本 → 原句走完整混合检索 → 变体逐条走纯向量检索 → 全部候选按 RRF 融合 → 截 `limit` 返回。
- **保序约束**：融合沿用 `reciprocal_rank_fusion`；向量路已命中的片段不被挤出名额（现有保护语义不变）。
- **可见范围**：`filters` 对每路一致下发，与单路检索同语义。
- **错误**：嵌入或检索失败照常抛出——不吞错；变体数量为 0 时等价于现有 `search`（纯向量部分无路可走时即单路行为）。

## 2. AgentRuntime.search_knowledge 行为契约

- **有变体生成器且成功**：`search_variants([原句, *variants], limit, filters)`；变体生成失败/超时/不可解析 → `search(原句, limit, filters)`（现状行为，零额外输出）。
- **无变体生成器（默认）**：行为与现状逐字一致（未配置时零变化）。
- **事件**：变体生成与多路融合按现有 tracer 口径落事件；失败回退落一条说明事件，不打 error 级。

## 3. 变体生成器协议（VariantRewriter）

```
async def rewrite(self, query: str) -> list[str]   # 0-3 条，语义不变
```

- 注入装配在 `AgentRuntime`（构造参数可选，None = 关闭）；实现复用现有 ChatModel 配置。
- 输出 JSON 数组；任何解析失败由调用方（runtime）判为回退，生成器自身不抛出业务错误。

## 4. 逐 ID 关键词路（FR-005 达标后实施形态）

- `store.py` 关键词 SQL：在现有整句 ILIKE + trigram 兜底上并集追加"查询内抽取的记录编号逐个 ILIKE 命中"候选；编号抽取复用 `record_ids()`。
- 不新增分词器/全文索引依赖；`KEYWORD_SIMILARITY_THRESHOLD` 与 `keyword_limit` 语义不变。
- 未达标时不实施，触发条件留档（见数据模型）。

## 5. 精排对照（FR-006，达标才进实施决策）

- 离线脚本 `rerank_offline.py`：`--archive <jsonl> --k <前缀k>` → 逐题读 `ranked`（≤20）→ rerank API 重排 → 输出重排前后 recall@k、逐题型分解、单次延迟分布与判定。
- API 形状先以最小请求验证（query + documents + top_n → results 按 relevance_score）；供应商与 embedding 同栈。
- 实施形态（达标后）：`search_variants` 尾部对融合结果前 20 重排取 `limit`；组件与配置入依赖清单。

## 6. 评测 CLI 契约

- `retrieval.py` 新增：`--variants`（生成并评测变体口径，调用现有模型配置）、`--archive <path>`（写 JSONL 存档）、`--top-n <20>`（存档深度）、`--id-probe`（ID 拼接口径 + 逐 ID 对照路评测，产出独立判定）。
- 新增口径/路径沿用现有报告框架（口径差值表 + 逐题型 + 判定行），判定线在报告内可见。
- 存档 schema 见 [data-model.md](../data-model.md) 检索存档节。

## 7. 验收门禁（实施部分 TDD）

先写可证伪失败用例再实现，覆盖：变体失败回退逐字一致、变体/原句去重、融合后条数与单路一致、融合保护名额、模型故障零错误输出、filters 每路一致。全量测试基线与原套件回归（011 SC-005 口径）通过后才能合入。
