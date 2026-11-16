"""测试用登录主体；固定值避免每个用例各写一份，也便于按角色断言边界。

内部主体：看全部会话与文档、无 scope 强制、记忆关闭——与 005 之前的默认行为一致。
商户主体：会话/文档/记忆都收敛到绑定账户。
"""

from flowpay.core.auth import Principal

INTERNAL = Principal(username="test_internal", role="internal")
MERCHANT = Principal(username="test_merchant", role="merchant", account_id="acct_test_merchant")
