"""生成黑马点评用户端模拟业务快照与运行流水。"""

from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal

UTC = "+08:00"
BASE = datetime(2026, 8, 1, 9, 0, 0)


def stamp(value: datetime) -> str:
    """输出固定时区的可读时间。"""
    return value.strftime("%Y-%m-%dT%H:%M:%S") + UTC


def hours_after(day: int, hour: int, minute: int = 0, second: int = 0) -> datetime:
    """构造 2026-08 内的固定时间。"""
    return datetime(2026, 8, day, hour, minute, second)


@dataclass
class Snapshot:
    """一次模拟生成的全部业务事实与运行事件。"""

    users: list[dict[str, object]] = field(default_factory=list)
    shops: list[dict[str, object]] = field(default_factory=list)
    vouchers: list[dict[str, object]] = field(default_factory=list)
    orders: list[dict[str, object]] = field(default_factory=list)
    reviews: list[dict[str, object]] = field(default_factory=list)
    incidents: list[dict[str, object]] = field(default_factory=list)
    seckill_attempts: list[dict[str, object]] = field(default_factory=list)
    order_events: list[dict[str, object]] = field(default_factory=list)
    shop_events: list[dict[str, object]] = field(default_factory=list)
    stock_changes: list[dict[str, object]] = field(default_factory=list)


SCALES: dict[str, dict[str, int]] = {
    "smoke": {
        "users": 32,
        "shops": 8,
        "vouchers": 10,
        "attempts_per_voucher": 28,
        "reviews": 180,
        "incidents": 3,
    },
    "standard": {
        "users": 240,
        "shops": 48,
        "vouchers": 64,
        "attempts_per_voucher": 72,
        "reviews": 1800,
        "incidents": 10,
    },
}


def build_snapshot(seed: int, scale: str) -> Snapshot:
    """按 seed 生成业务事实；同 seed 输出顺序与内容一致。"""
    config = SCALES.get(scale)
    if config is None:
        raise ValueError(f"unknown scale: {scale}; available: {', '.join(SCALES)}")
    rng = random.Random(seed)
    snapshot = Snapshot()
    claimed = defaultdict(int)
    order_seq = 0
    attempt_seq = 0
    event_seq = 0
    stock_seq = 0

    # 用户：预置题库角色，再生成普通用户。
    planted_users = [
        ("usr_u_000101", "用户-101", "北京"),
        ("usr_u_000102", "用户-102", "上海"),
        ("usr_u_000103", "用户-103", "广州"),
        ("usr_u_000104", "用户-104", "深圳"),
        ("usr_u_000201", "用户-201", "杭州"),
        ("usr_u_000202", "用户-202", "成都"),
        ("usr_u_000203", "用户-203", "南京"),
        ("usr_u_000204", "用户-204", "武汉"),
    ]
    for index, (user_id, nickname, city) in enumerate(planted_users, 1):
        snapshot.users.append(
            {
                "user_id": user_id,
                "nickname": nickname,
                "city": city,
                "registered_at": stamp(BASE + timedelta(days=index, hours=index)),
            }
        )
    for index in range(config["users"]):
        user_id = f"usr_u_{index + 1:06d}"
        if user_id in {row[0] for row in planted_users}:
            continue
        user_id = f"usr_u_{index + 1:06d}"
        snapshot.users.append(
            {
                "user_id": user_id,
                "nickname": f"点评用户{index + 1:04d}",
                "city": rng.choice(["北京", "上海", "广州", "深圳", "杭州", "成都"]),
                "registered_at": stamp(BASE - timedelta(days=rng.randint(30, 720))),
            }
        )

    # 门店：预置暂停店和低分店。
    categories = ["火锅", "奶茶", "日料", "烧烤", "咖啡", "快餐", "甜品", "自助餐"]
    planted_shops = [
        ("shp_s1_001", "云顶咖啡", categories[4], "朝阳区", "paused"),
        ("shp_s1_002", "夜航日料", categories[2], "静安区", "operating"),
        ("shp_r1_001", "江南小馆", categories[6], "鼓楼区", "operating"),
    ]
    for shop_id, name, category, region, status in planted_shops:
        snapshot.shops.append(
            {
                "shop_id": shop_id,
                "name": name,
                "category": category,
                "region": region,
                "address": f"{region}模拟路{rng.randint(1, 999)}号",
                "business_hours": "10:00-22:00",
                "status": status,
                "rating": 3.2 if shop_id == "shp_r1_001" else round(rng.uniform(4.2, 4.8), 1),
            }
        )
    for index in range(config["shops"]):
        shop_id = f"shp_g_{index + 1:06d}"
        category = categories[index % len(categories)]
        snapshot.shops.append(
            {
                "shop_id": shop_id,
                "name": f"{category}体验店{index + 1:03d}",
                "category": category,
                "region": rng.choice(["朝阳区", "静安区", "天河区", "高新区"]),
                "address": "模拟数据地址",
                "business_hours": "10:00-22:00",
                "status": "operating",
                "rating": round(rng.uniform(3.6, 4.9), 1),
            }
        )

    def add_attempt(
        *,
        user_id: str,
        voucher_id: str,
        occurred_at: datetime,
        result: str,
        error_code: str | None,
        stock_after: int,
    ) -> None:
        nonlocal attempt_seq
        attempt_seq += 1
        snapshot.seckill_attempts.append(
            {
                "attempt_id": f"req_s1_{attempt_seq:04d}",
                "user_id": user_id,
                "voucher_id": voucher_id,
                "result": result,
                "error_code": error_code,
                "created_at": stamp(occurred_at),
                "stock_after": stock_after,
            }
        )

    def add_stock_change(voucher_id: str, delta: int, after: int, at: datetime) -> None:
        nonlocal stock_seq
        stock_seq += 1
        snapshot.stock_changes.append(
            {
                "change_id": f"chg_s1_{stock_seq:04d}",
                "voucher_id": voucher_id,
                "delta": delta,
                "stock_after": after,
                "reason": "seckill_claim_success",
                "created_at": stamp(at),
            }
        )

    def add_order(
        *,
        user_id: str,
        voucher: dict[str, object],
        status: str,
        created_at: datetime,
        order_id: str | None = None,
        used_at: datetime | None = None,
        expired_at: datetime | None = None,
        refunded_at: datetime | None = None,
    ) -> str:
        nonlocal order_seq, event_seq
        order_seq += 1
        final_id = order_id or f"ord_g_{order_seq:06d}"
        snapshot.orders.append(
            {
                "order_id": final_id,
                "user_id": user_id,
                "voucher_id": voucher["voucher_id"],
                "shop_id": voucher["shop_id"],
                "status": status,
                "sale_price": voucher["sale_price"],
                "created_at": stamp(created_at),
                "used_at": stamp(used_at) if used_at else None,
                "expired_at": stamp(expired_at) if expired_at else None,
                "refunded_at": stamp(refunded_at) if refunded_at else None,
            }
        )
        event_seq += 1
        snapshot.order_events.append(
            {
                "event_id": f"evt_s1_{event_seq:05d}",
                "order_id": final_id,
                "user_id": user_id,
                "voucher_id": voucher["voucher_id"],
                "event": "order.created",
                "status": "created",
                "detail": "抢购成功并创建券订单",
                "created_at": stamp(created_at),
            }
        )
        if status == "used" and used_at:
            event_seq += 1
            snapshot.order_events.append(
                {
                    "event_id": f"evt_s1_{event_seq:05d}",
                    "order_id": final_id,
                    "user_id": user_id,
                    "voucher_id": voucher["voucher_id"],
                    "event": "order.used",
                    "status": "used",
                    "detail": "商户核销成功",
                    "created_at": stamp(used_at),
                }
            )
        if status == "expired" and expired_at:
            event_seq += 1
            snapshot.order_events.append(
                {
                    "event_id": f"evt_s1_{event_seq:05d}",
                    "order_id": final_id,
                    "user_id": user_id,
                    "voucher_id": voucher["voucher_id"],
                    "event": "order.expired",
                    "status": "expired",
                    "detail": "超过有效期未使用",
                    "created_at": stamp(expired_at),
                }
            )
        if status == "refund_rejected" and refunded_at:
            event_seq += 1
            snapshot.order_events.append(
                {
                    "event_id": f"evt_s1_{event_seq:05d}",
                    "order_id": final_id,
                    "user_id": user_id,
                    "voucher_id": voucher["voucher_id"],
                    "event": "refund.rejected",
                    "status": "refund_rejected",
                    "detail": "券已核销，不符合退款条件",
                    "created_at": stamp(refunded_at),
                }
            )
        if status == "refunded" and refunded_at:
            event_seq += 1
            snapshot.order_events.append(
                {
                    "event_id": f"evt_s1_{event_seq:05d}",
                    "order_id": final_id,
                    "user_id": user_id,
                    "voucher_id": voucher["voucher_id"],
                    "event": "refund.approved",
                    "status": "refunded",
                    "detail": "退款审核通过",
                    "created_at": stamp(refunded_at),
                }
            )
        if status == "canceled":
            event_seq += 1
            snapshot.order_events.append(
                {
                    "event_id": f"evt_s1_{event_seq:05d}",
                    "order_id": final_id,
                    "user_id": user_id,
                    "voucher_id": voucher["voucher_id"],
                    "event": "order.canceled",
                    "status": "canceled",
                    "detail": "用户取消订单",
                    "created_at": stamp(created_at + timedelta(minutes=5)),
                }
            )
        claimed[(user_id, voucher["voucher_id"])] += 1
        return final_id

    def make_voucher(
        *,
        voucher_id: str,
        shop_id: str,
        title: str,
        sale_type: Literal["seckill", "coupon"],
        total_stock: int,
        per_user_limit: int,
        sale_start: datetime,
        sale_end: datetime,
        valid_to: datetime,
        status: str,
    ) -> dict[str, object]:
        return {
            "voucher_id": voucher_id,
            "shop_id": shop_id,
            "title": title,
            "sale_type": sale_type,
            "face_value": 50 if sale_type == "seckill" else 30,
            "sale_price": 39 if sale_type == "seckill" else 19,
            "total_stock": total_stock,
            "remaining_stock": total_stock,
            "per_user_limit": per_user_limit,
            "sale_start_at": stamp(sale_start),
            "sale_end_at": stamp(sale_end),
            "valid_from": stamp(sale_start),
            "valid_to": stamp(valid_to),
            "status": status,
        }

    # 预置场景 1：库存售罄，用户请求失败且无订单。
    sale_start = hours_after(2, 12, 0)
    voucher = make_voucher(
        voucher_id="vou_s1_001",
        shop_id="shp_s1_002",
        title="夜航日料 50 元秒杀代金券",
        sale_type="seckill",
        total_stock=20,
        per_user_limit=1,
        sale_start=sale_start,
        sale_end=hours_after(2, 12, 30),
        valid_to=hours_after(9, 23, 59),
        status="sold_out",
    )
    snapshot.vouchers.append(voucher)
    for index in range(20):
        at = sale_start + timedelta(seconds=index * 11)
        add_attempt(
            user_id=f"usr_u_{index + 1:06d}",
            voucher_id=voucher["voucher_id"],
            occurred_at=at,
            result="success",
            error_code=None,
            stock_after=20 - index - 1,
        )
        add_stock_change(voucher["voucher_id"], -1, 20 - index - 1, at)
        add_order(
            user_id=f"usr_u_{index + 1:06d}",
            voucher=voucher,
            status="created",
            created_at=at,
            order_id=f"ord_s1_001_{index + 1:03d}",
        )
    voucher["remaining_stock"] = 0
    add_attempt(
        user_id="usr_u_000101",
        voucher_id=voucher["voucher_id"],
        occurred_at=hours_after(2, 12, 5),
        result="failed",
        error_code="VOUCHER_SOLD_OUT",
        stock_after=0,
    )

    # 预置场景 2：已有一个订单，第二次触发重复领取。
    voucher = make_voucher(
        voucher_id="vou_s1_002",
        shop_id="shp_g_000001",
        title="人气火锅 30 元门店券",
        sale_type="coupon",
        total_stock=200,
        per_user_limit=1,
        sale_start=hours_after(1, 9, 0),
        sale_end=hours_after(7, 22, 0),
        valid_to=hours_after(20, 23, 59),
        status="active",
    )
    snapshot.vouchers.append(voucher)
    add_order(
        user_id="usr_u_000102",
        voucher=voucher,
        status="created",
        created_at=hours_after(1, 10, 0),
        order_id="ord_s1_002",
    )
    add_attempt(
        user_id="usr_u_000102",
        voucher_id=voucher["voucher_id"],
        occurred_at=hours_after(1, 10, 5),
        result="failed",
        error_code="DUPLICATE_CLAIM",
        stock_after=199,
    )

    # 预置场景 3/4：活动未开始、活动已结束。
    voucher = make_voucher(
        voucher_id="vou_s1_003",
        shop_id="shp_g_000002",
        title="夏日烧烤秒杀券",
        sale_type="seckill",
        total_stock=100,
        per_user_limit=1,
        sale_start=hours_after(2, 12, 0),
        sale_end=hours_after(2, 13, 0),
        valid_to=hours_after(9, 23, 59),
        status="active",
    )
    snapshot.vouchers.append(voucher)
    add_attempt(
        user_id="usr_u_000103",
        voucher_id=voucher["voucher_id"],
        occurred_at=hours_after(2, 11, 58),
        result="failed",
        error_code="NOT_STARTED",
        stock_after=100,
    )

    voucher = make_voucher(
        voucher_id="vou_s1_004",
        shop_id="shp_g_000003",
        title="咖啡节早鸟秒杀券",
        sale_type="seckill",
        total_stock=80,
        per_user_limit=1,
        sale_start=hours_after(1, 10, 0),
        sale_end=hours_after(1, 20, 0),
        valid_to=hours_after(8, 23, 59),
        status="closed",
    )
    snapshot.vouchers.append(voucher)
    add_attempt(
        user_id="usr_u_000104",
        voucher_id=voucher["voucher_id"],
        occurred_at=hours_after(2, 9, 0),
        result="failed",
        error_code="SALE_CLOSED",
        stock_after=80,
    )

    # 预置订单状态场景。
    status_cases = [
        {
            "voucher_id": "vou_o1_001",
            "user_id": "usr_u_000201",
            "title": "可用代金券",
            "status": "used",
            "created_at": hours_after(1, 10),
            "used_at": hours_after(2, 18, 30),
        },
        {
            "voucher_id": "vou_o1_002",
            "user_id": "usr_u_000202",
            "title": "休闲下午茶券",
            "status": "created",
            "created_at": hours_after(1, 11),
        },
        {
            "voucher_id": "vou_o1_003",
            "user_id": "usr_u_000203",
            "title": "短期体验券",
            "status": "expired",
            "created_at": hours_after(1, 12),
            "valid_to": hours_after(2, 23, 59),
            "expired_at": hours_after(3, 0),
        },
        {
            "voucher_id": "vou_o1_004",
            "user_id": "usr_u_000204",
            "title": "已核销退款券",
            "status": "refund_rejected",
            "created_at": hours_after(1, 13),
            "used_at": hours_after(2, 12),
            "refunded_at": hours_after(3, 10),
        },
    ]
    for case in status_cases:
        voucher = make_voucher(
            voucher_id=str(case["voucher_id"]),
            shop_id="shp_g_000004",
            title=str(case["title"]),
            sale_type="coupon",
            total_stock=300,
            per_user_limit=2,
            sale_start=hours_after(1, 9),
            sale_end=hours_after(7, 22),
            valid_to=case.get("valid_to", hours_after(30, 23, 59)),
            status="active",
        )
        snapshot.vouchers.append(voucher)
        add_order(
            user_id=str(case["user_id"]),
            voucher=voucher,
            status=str(case["status"]),
            created_at=case["created_at"],
            order_id=f"ord_{str(case['voucher_id']).replace('vou_', '')}",
            used_at=case.get("used_at"),
            expired_at=case.get("expired_at"),
            refunded_at=case.get("refunded_at"),
        )

    # 预置门店暂停事件与低分评价。
    snapshot.shop_events.append(
        {
            "event_id": "shp_evt_s1_0001",
            "shop_id": "shp_s1_001",
            "event": "shop.paused",
            "status": "paused",
            "started_at": stamp(hours_after(3, 19, 0)),
            "ended_at": None,
            "detail": "设备维护，暂停核销",
        }
    )
    for index in range(12):
        snapshot.reviews.append(
            {
                "review_id": f"rev_s1_{index + 1:04d}",
                "user_id": f"usr_u_{index + 1:06d}",
                "shop_id": "shp_r1_001",
                "rating": rng.choice([1, 1, 2, 2, 3]),
                "content": "模拟差评：出餐慢、服务体验一般。",
                "created_at": stamp(hours_after(2, 12) + timedelta(minutes=index * 20)),
            }
        )

    # 常规活动、请求、订单。
    for index in range(config["vouchers"]):
        shop = snapshot.shops[index % len(snapshot.shops)]
        sale_start = BASE + timedelta(days=index % 6, hours=(index * 3) % 12)
        sale_end = sale_start + timedelta(hours=8)
        valid_to = sale_end + timedelta(days=12)
        is_seckill = index % 2 == 0
        voucher = make_voucher(
            voucher_id=f"vou_g_{index + 1:05d}",
            shop_id=str(shop["shop_id"]),
            title=f"{'限时秒杀' if is_seckill else '门店优惠'}券{index + 1:03d}",
            sale_type="seckill" if is_seckill else "coupon",
            total_stock=60 if is_seckill else 500,
            per_user_limit=1 if is_seckill else 2,
            sale_start=sale_start,
            sale_end=sale_end,
            valid_to=valid_to,
            status="active",
        )
        snapshot.vouchers.append(voucher)
        sold_out = is_seckill and index % 4 == 1
        if sold_out:
            voucher["status"] = "sold_out"
        user_ids = [str(row["user_id"]) for row in snapshot.users]
        for attempt_index in range(config["attempts_per_voucher"]):
            user_id = rng.choice(user_ids)
            offset = rng.randint(0, max(1, int((sale_end - sale_start).total_seconds())))
            at = sale_start + timedelta(seconds=offset)
            key = (user_id, voucher["voucher_id"])
            if claimed[key] >= int(voucher["per_user_limit"]):
                add_attempt(
                    user_id=user_id,
                    voucher_id=voucher["voucher_id"],
                    occurred_at=at,
                    result="failed",
                    error_code="DUPLICATE_CLAIM",
                    stock_after=int(voucher["remaining_stock"]),
                )
            elif int(voucher["remaining_stock"]) <= 0:
                add_attempt(
                    user_id=user_id,
                    voucher_id=voucher["voucher_id"],
                    occurred_at=at,
                    result="failed",
                    error_code="VOUCHER_SOLD_OUT",
                    stock_after=0,
                )
            elif rng.random() < 0.08:
                add_attempt(
                    user_id=user_id,
                    voucher_id=voucher["voucher_id"],
                    occurred_at=at,
                    result="failed",
                    error_code=rng.choice(["SYSTEM_BUSY", "PAY_TIMEOUT"]),
                    stock_after=int(voucher["remaining_stock"]),
                )
            else:
                voucher["remaining_stock"] = int(voucher["remaining_stock"]) - 1
                add_attempt(
                    user_id=user_id,
                    voucher_id=voucher["voucher_id"],
                    occurred_at=at,
                    result="success",
                    error_code=None,
                    stock_after=int(voucher["remaining_stock"]),
                )
                add_stock_change(
                    voucher["voucher_id"], -1, int(voucher["remaining_stock"]), at
                )
                status = rng.choices(
                    ["created", "used", "expired", "refunded", "canceled"],
                    weights=[55, 25, 8, 8, 4],
                    k=1,
                )[0]
                used_at = at + timedelta(hours=rng.randint(12, 72)) if status == "used" else None
                expired_at = valid_to + timedelta(hours=1) if status == "expired" else None
                refunded_at = at + timedelta(hours=6) if status == "refunded" else None
                add_order(
                    user_id=user_id,
                    voucher=voucher,
                    status=status,
                    created_at=at,
                    used_at=used_at,
                    expired_at=expired_at,
                    refunded_at=refunded_at,
                )

    # 评价与事故。
    for index in range(config["reviews"]):
        user = snapshot.users[index % len(snapshot.users)]
        shop = snapshot.shops[index % len(snapshot.shops)]
        snapshot.reviews.append(
            {
                "review_id": f"rev_g_{index + 1:05d}",
                "user_id": str(user["user_id"]),
                "shop_id": str(shop["shop_id"]),
                "rating": rng.choices([1, 2, 3, 4, 5], weights=[6, 9, 15, 25, 45], k=1)[0],
                "content": rng.choice(
                    [
                        "模拟评价：味道不错。",
                        "模拟评价：服务很好。",
                        "模拟评价：位置方便。",
                        "模拟评价：等待时间偏长。",
                        "模拟评价：性价比一般。",
                    ]
                ),
                "created_at": stamp(BASE + timedelta(minutes=index * 37)),
            }
        )
    for index in range(config["incidents"]):
        start = BASE + timedelta(hours=index * 17)
        snapshot.incidents.append(
            {
                "incident_id": f"inc_s1_{index + 1:03d}",
                "service": rng.choice(["seckill", "order", "payment", "verification"]),
                "region": rng.choice(["华北", "华东", "华南"]),
                "status": "resolved" if index % 2 == 0 else "ongoing",
                "started_at": stamp(start),
                "ended_at": stamp(start + timedelta(minutes=35)) if index % 2 == 0 else None,
                "detail": "模拟平台事故：可能影响请求结果或状态同步。",
            }
        )

    # 门店常规状态事件。
    for index, shop in enumerate(snapshot.shops[3:], 4):
        event_at = BASE + timedelta(days=index % 5, hours=8)
        snapshot.shop_events.append(
            {
                "event_id": f"shp_evt_g_{index:04d}",
                "shop_id": str(shop["shop_id"]),
                "event": "shop.opened" if index % 2 == 0 else "shop.status_confirmed",
                "status": "operating",
                "started_at": stamp(event_at),
                "ended_at": None,
                "detail": "模拟门店状态事件",
            }
        )
    return snapshot
