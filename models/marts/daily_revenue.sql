{{
    config(
        materialized='incremental',
        unique_key='order_date',
        incremental_strategy='delete+insert'
    )
}}

{#-
    Daily revenue excluding returned orders. Incremental: on re-runs only the
    most recent days are recomputed (a lookback window catches late-arriving
    payments), and delete+insert on order_date keeps the table idempotent.
-#}

with orders as (

    select * from {{ ref('orders') }}
    where status not in ('returned', 'return_pending')

    {% if is_incremental() %}
      and order_date >= (
          select coalesce(max(order_date), '1900-01-01'::date) - interval '{{ var("revenue_lookback_days") }} days'
          from {{ this }}
      )
    {% endif %}

)

select
    order_date,
    count(order_id) as number_of_orders,
    count(distinct customer_id) as number_of_customers,
    sum(amount) as total_revenue,
    sum(amount) / count(order_id) as average_order_value

from orders
group by order_date
