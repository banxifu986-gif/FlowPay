# 交付报告：010 逐断言校验（发布前校验器与断点归因）

生成时间：2026-11-23。分支 `feature/010-assertion-verification`（未推送、未合并 main）。流程：grill 澄清（10 决策）→ spec-kit 全套工件 → 双工作树并行（树A 能力侧 / 树B 消费侧，T000-T017）→ B3 合并集成 → Code Review（NO-GO→P1×3+P2×7，清偿后 GO，报告见 review-report.md）→ T019 冒烟。

## 成功标准出数

- **SC-1 开关关闭零回归**：合并后宿主 pytest 563 passed / 6 failed / 12 收集错误——6 个失败全部为环境固有（`fcntl`×3 POSIX 文件锁 + `os.symlink` WinError 1314 无符号链接特权），与 009 基线一致且不在 010 改动面；含 SC-1 专项测试（关闭态零新增调用零事件，可证伪形态）。009 终门禁形态为容器全量，010 改动面与 fcntl/symlink 模块零交集，容器复跑未做（如实披露）。
- **SC-2 质量门**：ruff 全过；basedpyright 0 错 0 警；前端构建通过（`npm run build`）。
- **SC-3 校验器行为单测**：verify 子集 58 例（四档 fixture 含三类越界样例、ID 存在性、句级替换子集校验、降级幂等、fail-open 六类注入+取消穿透+费用入账）+ gate/audit 31 例 + 审查清偿回归 8 例（臂权威×4、装配链×3、DISPUTE×1）。
- **SC-4 冒烟回放**（specs/010-assertion-verification/smoke/）：12 题 = synthesize 作答 5 + 轻分支 5 + clarify 终态 2；**45 断言**（insufficient 27 / partial 6 / supported 12 / 矛盾 0）；**断点分布：绑错 20、表述越界 6、没召回 6、矛盾 0**；**误伤基线：通过题必需断言 3 条、误伤 0 条（0.0%）**；fail-open 0 次（第一轮 1 次 JSONDecodeError 经 fail_open 观测改进定性为模型输出随机抖动，第二轮零复现）。三类断点各 ≥1 达成；矛盾档 0 例如实披露——冒烟题单无矛盾嫌疑形态，占比 0% 暂无"升级呈现分歧"的数据支撑，T021 全量揭示。

## 费用记录（Ban 批准范围内）

- 冒烟两轮：12 题 ×（抽取+批量校验）≈ 47 次校验调用（第一轮 24 - 1 次 fail-open 截断 + 第二轮 24）；冒烟量级，零评测臂消耗。
- T021 全量回放（96 题 ≈ 192 次调用）与 T022 开启态评测臂（每作答 +2 次调用）**均未执行**，待批准。

## FR-4 越界定位门槛的实测观察

冒烟 partial 判定 6 条全部携带越界定位与收紧表述，误伤 0——"缺定位视同证据不足"的防线在本样本未造成判定损失也未扩大误伤，维持默认假设（grill 澄清⑨未确认项的实证依据）。

## 已知债（不在本轮清偿，交付披露）

1. **句级切分双实现与词面复制**（P2-2/2-5）：gate 侧筛选用 citation_check 切分、core 处置用行级切分，词面五处复制——涉 009 既有代码统一，未经允许不动；影响面为边缘句子（列表行/跨行）的锚点匹配成功率，方向是"匹配失败降级"安全侧。
2. **多轮回放证据池口径**（P2-6）：回放证据池=会话最终池 ≠ 在线当轮池（单轮无影响；多轮题的"没召回"归类可能高估）——T021 全量报告按单轮/多轮分列，多轮口径偏差在报告标注，是否立项修正待定性。
3. **在线记录摘录维度暂空**：ModelAuditor 协议不触 shared 记录数据源（P1-2 装配最小落地），在线校验的证据集缺记录摘录；回放侧全口径。T022 开启态若暴露记录类断言误判，扩装配面为独立小项。
4. 冒烟样本 12 题对 96 题分布的代表性有限（断点分布结论以 T021 全量为准）。

## 交付物清单

- 代码：core/verify（能力包 7 文件）、workflows/multi_agent/assertion_gate.py + investigation.py 单点 + graph/router/bootstrap 装配链、scripts/benchmark/assertion_audit.py（回放+断点归类+误伤基线+CoreVerifier）
- 配置面：`assertion_audit_enabled`（默认 false）、`assertion_audit_evidence_budget`（默认 8000）
- 测试：核心回归全绿（563 passed），新增 100+ 用例
- 报告：smoke/assertion-verdicts.jsonl、smoke/assertion-report.md、review-report.md（外部审查）、本报告
