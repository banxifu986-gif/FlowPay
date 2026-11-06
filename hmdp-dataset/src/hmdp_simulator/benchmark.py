"""黑马点评用户端 Benchmark 蓝图；冻结题目顺序，便于后续评测复现。"""

from __future__ import annotations

import json


def blueprints() -> list[dict[str, object]]:
    """返回 18 道冻结题；其中 7 道多轮题。"""
    cases: list[dict[str, object]] = [
        {
            "question_id": "HMDP-BM-SR-0001",
            "question_type": "simple_retrieval",
            "difficulty": 1,
            "multi_turn": False,
            "question": "代金券、折扣券和秒杀券有什么区别？",
            "required_conditions": [],
            "ground_truth": {
                "required_assertions": [
                    "代金券满足门槛后抵扣固定金额；折扣券按活动折扣计算；秒杀券是低库存低价的特殊代金券。"
                ]
            },
            "required_documents": ["HMDP-BR-001"],
            "required_tools": ["search_documents"],
        },
        {
            "question_id": "HMDP-BM-SR-0002",
            "question_type": "simple_retrieval",
            "difficulty": 1,
            "multi_turn": False,
            "question": "门店券可以在其他门店使用吗？",
            "required_conditions": [],
            "ground_truth": {
                "required_assertions": ["门店券只属于发券门店，不能跨店使用。"],
                "negative_assertions": ["不能说所有券都能跨店使用。"]
            },
            "required_documents": ["HMDP-BR-001", "HMDP-BR-006"],
            "required_tools": ["search_documents"],
        },
        {
            "question_id": "HMDP-BM-TM-0003",
            "question_type": "temporal_versioned",
            "difficulty": 2,
            "multi_turn": False,
            "question": "我 8 月 3 日看到一张券，8 月 5 日又说不能用了，可能是过期还是活动结束？",
            "required_conditions": ["voucher_id"],
            "ground_truth": {
                "required_assertions": [
                    "需要区分券订单有效期和券销售窗口；订单过期后不能再使用，活动结束后不能再抢。"
                ]
            },
            "required_documents": ["HMDP-BR-003", "HMDP-BR-008"],
            "required_tools": ["search_documents", "query_voucher", "query_voucher_order"],
        },
        {
            "question_id": "HMDP-BM-MD-0004",
            "question_type": "multi_document",
            "difficulty": 2,
            "multi_turn": False,
            "question": "用户显示已抢完但没有订单，应该按什么步骤确认？",
            "required_conditions": ["user_id", "voucher_id"],
            "ground_truth": {
                "required_assertions": [
                    "先查当前用户请求，再查库存流水和订单表；VOUCHER_SOLD_OUT 不会生成订单。"
                ]
            },
            "required_documents": ["HMDP-BR-004", "HMDP-TS-001", "HMDP-EV-001"],
            "required_tools": ["search_documents", "get_seckill_attempt", "get_stock_change", "query_voucher_order"],
        },
        {
            "question_id": "HMDP-BM-TD-0005",
            "question_type": "tool_use_diagnostic",
            "difficulty": 2,
            "multi_turn": False,
            "question": "我的券订单显示已使用，但我印象中没有到店，应该怎么核实？",
            "required_conditions": ["user_id", "order_id"],
            "ground_truth": {
                "required_assertions": [
                    "以订单状态和核销/订单事件为准；不能只按用户印象判断。"
                ]
            },
            "required_documents": ["HMDP-BR-005", "HMDP-BR-006"],
            "required_tools": ["query_voucher_order", "get_order_event"],
        },
        {
            "question_id": "HMDP-BM-MS-0006",
            "question_type": "multi_source",
            "difficulty": 3,
            "multi_turn": False,
            "question": "为什么店铺评分最近下降了？",
            "required_conditions": ["shop_id"],
            "ground_truth": {
                "required_assertions": [
                    "需要查看门店当前评分、评价分布和评价时间；评分下降只能解释为评价统计变化，不能证明订单异常。"
                ]
            },
            "required_documents": ["HMDP-BR-009", "HMDP-TS-005"],
            "required_tools": ["query_shop", "query_reviews"],
        },
        {
            "question_id": "HMDP-BM-MH-0007",
            "question_type": "multi_hop",
            "difficulty": 3,
            "multi_turn": True,
            "initial_user_message": "我刚才抢券失败了，帮我看看为什么。",
            "clarification_plan": [
                {"missing": ["voucher_id"], "question": "请提供券名称、活动名称或券编号。"},
                {"missing": ["occurred_at"], "question": "大概什么时候抢的？"},
                {"missing": ["user_id"], "question": "请确认当前登录用户编号。"}
            ],
            "available_user_facts": {
                "voucher_id": "vou_s1_001",
                "occurred_at": "2026-08-02 12:05",
                "user_id": "usr_u_000101"
            },
            "ground_truth": {
                "required_assertions": [
                    "usr_u_000101 在 2026-08-02 12:05 请求 vou_s1_001，结果为 VOUCHER_SOLD_OUT。",
                    "该请求不会创建订单；不能把抢购失败说成订单存在。"
                ],
                "negative_assertions": ["不能说请求成功。"]
            },
            "required_documents": ["HMDP-BR-003", "HMDP-BR-004", "HMDP-TS-001"],
            "required_tools": ["get_seckill_attempt", "get_stock_change", "query_voucher_order"]
        },
        {
            "question_id": "HMDP-BM-MH-0008",
            "question_type": "multi_hop",
            "difficulty": 3,
            "multi_turn": True,
            "initial_user_message": "平台说我重复领取了，可我没看到券，怎么回事？",
            "clarification_plan": [
                {"missing": ["voucher_id"], "question": "请提供券名称或券编号。"},
                {"missing": ["user_id"], "question": "请确认当前登录用户编号。"}
            ],
            "available_user_facts": {"voucher_id": "vou_s1_002", "user_id": "usr_u_000102"},
            "ground_truth": {
                "required_assertions": [
                    "usr_u_000102 在 vou_s1_002 已有一个有效订单，后续请求返回 DUPLICATE_CLAIM。",
                    "重复领取表示限购资格已占用，不表示没有抢到过。"
                ]
            },
            "required_documents": ["HMDP-BR-002", "HMDP-TS-002"],
            "required_tools": ["get_seckill_attempt", "query_voucher_order"]
        },
        {
            "question_id": "HMDP-BM-TM-0009",
            "question_type": "temporal_versioned",
            "difficulty": 2,
            "multi_turn": True,
            "initial_user_message": "我还没到活动开始就点了抢购，系统没反应。",
            "clarification_plan": [
                {"missing": ["voucher_id"], "question": "请提供券名称或券编号。"},
                {"missing": ["occurred_at"], "question": "大概什么时间点的？"},
                {"missing": ["user_id"], "question": "请确认当前登录用户编号。"}
            ],
            "available_user_facts": {
                "voucher_id": "vou_s1_003",
                "occurred_at": "2026-08-02 11:58",
                "user_id": "usr_u_000103"
            },
            "ground_truth": {
                "required_assertions": [
                    "请求发生在活动开始前，结果为 NOT_STARTED。",
                    "该请求不扣库存，也不会创建订单。"
                ]
            },
            "required_documents": ["HMDP-BR-003", "HMDP-TS-003"],
            "required_tools": ["get_seckill_attempt", "query_voucher"]
        },
        {
            "question_id": "HMDP-BM-TM-0010",
            "question_type": "temporal_versioned",
            "difficulty": 2,
            "multi_turn": True,
            "initial_user_message": "活动早就结束了，我又点了一次，提示不能买。",
            "clarification_plan": [
                {"missing": ["voucher_id"], "question": "请提供券名称或券编号。"},
                {"missing": ["occurred_at"], "question": "大概什么时间点的？"},
                {"missing": ["user_id"], "question": "请确认当前登录用户编号。"}
            ],
            "available_user_facts": {
                "voucher_id": "vou_s1_004",
                "occurred_at": "2026-08-02 09:00",
                "user_id": "usr_u_000104"
            },
            "ground_truth": {
                "required_assertions": [
                    "请求发生在销售结束后，结果为 SALE_CLOSED。",
                    "该请求不扣库存，也不会创建订单。"
                ]
            },
            "required_documents": ["HMDP-BR-003", "HMDP-TS-003"],
            "required_tools": ["get_seckill_attempt", "query_voucher"]
        },
        {
            "question_id": "HMDP-BM-MS-0011",
            "question_type": "multi_source",
            "difficulty": 3,
            "multi_turn": True,
            "initial_user_message": "我的订单现在是什么状态？",
            "clarification_plan": [
                {"missing": ["order_id"], "question": "请提供订单编号。"},
                {"missing": ["user_id"], "question": "请确认当前登录用户编号。"}
            ],
            "available_user_facts": {"order_id": "ord_o1_001", "user_id": "usr_u_000201"},
            "ground_truth": {
                "required_assertions": [
                    "ord_o1_001 属于 usr_u_000201，状态为 used。",
                    "回答应引用订单状态和相关事件，而不是只根据券规则推断。"
                ]
            },
            "required_documents": ["HMDP-BR-005", "HMDP-EV-001"],
            "required_tools": ["query_voucher_order", "get_order_event"]
        },
        {
            "question_id": "HMDP-BM-MS-0012",
            "question_type": "multi_source",
            "difficulty": 2,
            "multi_turn": True,
            "initial_user_message": "我有张券还没用，之后还能用吗？",
            "clarification_plan": [
                {"missing": ["order_id"], "question": "请提供订单编号。"},
                {"missing": ["user_id"], "question": "请确认当前登录用户编号。"}
            ],
            "available_user_facts": {"order_id": "ord_o1_002", "user_id": "usr_u_000202"},
            "ground_truth": {
                "required_assertions": [
                    "ord_o1_002 为 created，且在券有效期内；在有效期内、门店正常时仍可使用。"
                ]
            },
            "required_documents": ["HMDP-BR-005", "HMDP-BR-006", "HMDP-BR-008"],
            "required_tools": ["query_voucher_order", "query_voucher", "query_shop"]
        },
        {
            "question_id": "HMDP-BM-MD-0013",
            "question_type": "multi_document",
            "difficulty": 3,
            "multi_turn": True,
            "initial_user_message": "我这张券过期了，还能恢复吗？",
            "clarification_plan": [
                {"missing": ["order_id"], "question": "请提供订单编号。"},
                {"missing": ["user_id"], "question": "请确认当前登录用户编号。"}
            ],
            "available_user_facts": {"order_id": "ord_o1_003", "user_id": "usr_u_000203"},
            "ground_truth": {
                "required_assertions": [
                    "ord_o1_003 已过期；过期券不能直接恢复，是否可退款需看退款规则。"
                ],
                "negative_assertions": ["不能说过期券一定能退款或恢复使用。"]
            },
            "required_documents": ["HMDP-BR-005", "HMDP-BR-007", "HMDP-BR-008"],
            "required_tools": ["query_voucher_order", "get_order_event", "search_documents"]
        },
        {
            "question_id": "HMDP-BM-TD-0014",
            "question_type": "tool_use_diagnostic",
            "difficulty": 3,
            "multi_turn": True,
            "initial_user_message": "我的退款被拒绝了，为什么？",
            "clarification_plan": [
                {"missing": ["order_id"], "question": "请提供订单编号。"},
                {"missing": ["user_id"], "question": "请确认当前登录用户编号。"}
            ],
            "available_user_facts": {"order_id": "ord_o1_004", "user_id": "usr_u_000204"},
            "ground_truth": {
                "required_assertions": [
                    "ord_o1_004 已被使用，退款申请返回 refund_rejected。",
                    "原因是券已核销，不能退款。"
                ]
            },
            "required_documents": ["HMDP-BR-005", "HMDP-BR-007"],
            "required_tools": ["query_voucher_order", "get_order_event", "search_documents"]
        },
        {
            "question_id": "HMDP-BM-MS-0015",
            "question_type": "multi_source",
            "difficulty": 2,
            "multi_turn": True,
            "initial_user_message": "为什么这家店不能核销？",
            "clarification_plan": [
                {"missing": ["shop_id"], "question": "请提供店铺名称或店铺编号。"},
                {"missing": ["occurred_at"], "question": "大概什么时候去的？"}
            ],
            "available_user_facts": {
                "shop_id": "shp_s1_001",
                "occurred_at": "2026-08-03 19:30"
            },
            "ground_truth": {
                "required_assertions": [
                    "shp_s1_001 在 2026-08-03 19:00 后处于 paused 状态。",
                    "暂停营业期间不能核销，与券本身是否有效无关。"
                ]
            },
            "required_documents": ["HMDP-BR-006", "HMDP-BR-010", "HMDP-BR-011"],
            "required_tools": ["query_shop", "get_shop_event"]
        },
        {
            "question_id": "HMDP-BM-SR-0016",
            "question_type": "simple_retrieval",
            "difficulty": 1,
            "multi_turn": False,
            "question": "平台事故能证明我的订单失败吗？",
            "required_conditions": [],
            "ground_truth": {
                "required_assertions": [
                    "事故只能解释时间重叠的异常，不能自动证明某笔订单成功或失败。"
                ]
            },
            "required_documents": ["HMDP-BR-012", "HMDP-EV-001"],
            "required_tools": ["search_documents", "list_incidents"]
        },
        {
            "question_id": "HMDP-BM-MD-0017",
            "question_type": "multi_document",
            "difficulty": 2,
            "multi_turn": False,
            "question": "用户助手可以查别人的订单吗？",
            "required_conditions": [],
            "ground_truth": {
                "required_assertions": [
                    "用户助手只处理当前登录用户自己的数据，不能读取其他用户订单。"
                ]
            },
            "required_documents": ["HMDP-INTRO-001", "HMDP-EV-002"],
            "required_tools": ["search_documents"]
        },
        {
            "question_id": "HMDP-BM-MS-0018",
            "question_type": "multi_source",
            "difficulty": 2,
            "multi_turn": True,
            "initial_user_message": "江南小馆最近的评价怎么样？为什么分数不高？",
            "clarification_plan": [
                {"missing": ["shop_id"], "question": "请提供店铺名称或店铺编号。"}
            ],
            "available_user_facts": {"shop_id": "shp_r1_001"},
            "ground_truth": {
                "required_assertions": [
                    "shp_r1_001 当前评分较低。",
                    "低分评价集中在出餐慢和服务体验一般等体验问题。",
                    "评分统计变化不能证明某笔券订单异常。"
                ]
            },
            "required_documents": ["HMDP-BR-009", "HMDP-TS-005"],
            "required_tools": ["query_shop", "query_reviews"]
        },
    ]
    return cases


def write_benchmarks(path) -> int:
    """写出蓝图 JSONL，返回题数。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = blueprints()
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return len(rows)

