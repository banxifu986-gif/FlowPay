# 券订单生命周期

用户抢券成功后，订单通常进入 created 状态。
用户到店核销后变为 used。
超过有效期未使用会变为 expired。
用户取消后变为 canceled；取消订单不再占用券或限购资格。
退款申请会进入 refund_pending，审核通过是 refunded，审核拒绝是 refund_rejected。
