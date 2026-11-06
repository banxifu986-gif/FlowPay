# 领取与限购规则

每张券有独立的每人限购数量。
用户已拥有同券有效订单的数量达到限购后，新请求返回 DUPLICATE_CLAIM。
限购通常统计 created、used、refund_pending、refunded 状态的订单；canceled 订单不占用资格。
同一请求重复提交不会创建两笔订单。
