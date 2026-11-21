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

with
{% if is_incremental() %}
watermark as (

    select
        {{ dbt.dateadd(
            'day',
            -var('revenue_lookback_days'),
            "coalesce(max(order_date), cast('1900-01-01' as date))"
        ) }} as cutoff
    from {{ this }}

),

{% endif %}
orders as (

    select o.*
    from {{ ref('orders') }} as o
    {% if is_incremental() %}
    cross join watermark as w
    {% endif %}
    where o.status not in ('returned', 'return_pending')
    {% if is_incremental() %}
        and o.order_date >= w.cutoff
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
