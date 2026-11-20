-- Singular test: an order's total amount must equal the sum of its per-method amounts.
-- Returns offending rows; the test passes when none are returned.
select
    order_id,
    amount,
    credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount as method_sum
from {{ ref('orders') }}
where amount is not null
  and abs(amount - (credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount)) > 0.001
