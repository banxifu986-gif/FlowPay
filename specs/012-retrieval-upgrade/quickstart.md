# 快速验收：检索链路优化（012）

## 前置

- 知识集合就绪（现套件或 FAQ collection），embedding 配置与集合向量空间一致。
- 模型配置可用（变体生成与 007 评测共用现有配置）。
- 精排对照另需同栈 rerank API 密钥（仅④阶段）。

## ① 离线变体实验（旧 96 题）

```bash
python scripts/benchmark/retrieval.py --variants --archive var/benchmark/variants-archive.jsonl
```

预期：报告新增变体口径行与 initial 差值；判定行给出 ≥+10pp 与单轮不降结论；存档 JSONL 生成且含变体文本。复跑同一命令 → 变体读存档不重复调模型，报告结构一致。

## ② 变体实施后回归

```bash
pytest backend/tests -q        # 全量基线：755 passed/5 skipped（以当次记录为准）
```

预期：新增门禁用例全绿（回退逐字一致 / 去重 / 条数一致 / 融合保护 / 故障零输出）；原套件回归检查（011 SC-005 口径）通过。

## ③ ID 对照实验

```bash
python scripts/benchmark/retrieval.py --id-probe
```

预期：报告含 ID 拼接口径下现有关键词路 vs 逐 ID 对照路的 recall@k 对比与判定；达标结论写明实施或不达标写明触发条件。

## ④ 精排对照

```bash
python scripts/benchmark/rerank_offline.py --archive var/benchmark/variants-archive.jsonl
```

预期：报告给出重排前后 recall@k（消费存档，不重跑检索）、逐题型分解、单次延迟分布；判定 ≥+5pp 且 P50 ≤500ms，未达标写「维持现状 + 触发条件」。

## ⑤⑥ FAQ 补测与端到端对比

- FAQ dev 40 题就绪：①③④同一命令换 `--config`（FAQ 集合）复跑，结论并列。
- 011 正式基线就绪后：用 011 runner 复跑 60 题 test，对比报告标注两侧检索配置版本；异常题单列。

## 结果判读

- 任一实验未达标：对应实施不发生，结论与触发条件留档——这不是失败，是设计的出口之一。
- 全部达标：端到端对比报告（⑥）是最终效果证据，离线 recall 增益不单独冒充端到端收益。
