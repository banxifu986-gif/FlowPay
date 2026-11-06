# 黑马点评用户端模拟数据包

这是面向普通用户智能助手 PoC 的独立数据集，不依赖黑马点评真实代码。

## 生成

在仓库根目录执行：

```powershell
$env:PYTHONPATH = "hmdp-dataset/src"
python -m hmdp_simulator.generate --root hmdp-dataset --scale standard
```

复现测试：

```powershell
python -m unittest discover -s hmdp-dataset/tests
```

## PoC 校验

宿主机验证：

```powershell
$env:PYTHONPATH = "hmdp-dataset/src"
python -m hmdp_simulator.poc --root hmdp-dataset
```

Docker 后端容器验证：

```powershell
docker compose exec -T backend sh -lc `
  "PYTHONPATH=/hmdp-dataset/src python -m hmdp_simulator.poc --root /hmdp-dataset"
```

校验内容包括：

- manifest、知识文档、Benchmark 是否存在；
- SQLite 与 JSONL 行数是否一致；
- 已抢完、重复领取、活动未开始、活动已结束场景；
- 订单使用、未使用、过期、退款拒绝状态；
- 门店暂停和低评分门店场景。

`--scale smoke` 适合快速联调，`--scale standard` 适合本地评测。

## 数据布局

```text
data/knowledge/                  规则与排查文档
data/generated/canonical/
  hmdp.sqlite                    用户、店铺、券、订单、评价、事故与事件
  runtime/seckill_attempts.jsonl 抢券请求流水
  runtime/order_events.jsonl     订单与退款事件
  runtime/shop_events.jsonl      门店状态事件
  runtime/stock_changes.jsonl    券库存流水
  manifest.json                  数量、哈希与可重放清单
benchmark/design/
  benchmark_blueprints.jsonl     18 道冻结评测题
```

## 核心实体

- 用户：`users`
- 门店：`shops`
- 券：`vouchers`
- 券订单：`voucher_orders`
- 评价：`reviews`
- 抢券请求：`seckill_attempts`
- 订单事件：`order_events`
- 门店事件：`shop_events`
- 库存流水：`stock_changes`
- 平台事故：`incidents`

## 预置排查场景

| 场景 | 关键实体 |
| --- | --- |
| 券已抢完，请求失败且无订单 | `vou_s1_001` / `usr_u_000101` |
| 重复领取 | `vou_s1_002` / `usr_u_000102` / `ord_s1_002` |
| 活动未开始 | `vou_s1_003` / `usr_u_000103` |
| 活动已结束 | `vou_s1_004` / `usr_u_000104` |
| 订单已使用 | `vou_o1_001` / `ord_o1_001` |
| 订单未使用 | `vou_o1_002` / `ord_o1_002` |
| 订单已过期 | `vou_o1_003` / `ord_o1_003` |
| 已核销退款被拒 | `vou_o1_004` / `ord_o1_004` |
| 门店暂停核销 | `shp_s1_001` |
| 门店评分下降 | `shp_r1_001` |

## 使用边界

用户助手只能查当前登录用户自己的数据。实际接入黑马点评时，`user_id`
应从登录态或业务服务获取，不应让用户随意提供并查询他人数据。

