{#- Share of revenue by payment method, built with a Jinja loop over unpivoted columns. -#}

{% set payment_methods = ['credit_card', 'coupon', 'bank_transfer', 'gift_card'] %}

with orders as (

    select * from {{ ref('orders') }}

),

unpivoted as (

    {% for method in payment_methods %}
    select
        '{{ method }}' as payment_method,
        {{ method }}_amount as amount
    from orders
    {% if not loop.last %}union all{% endif %}
    {% endfor %}

),

totals as (

    select
        payment_method,
        sum(amount) as total_amount,
        sum(case when amount > 0 then 1 else 0 end) as number_of_payments
    from unpivoted
    group by payment_method

)

select
    payment_method,
    total_amount,
    number_of_payments,
    cast(total_amount as float) / sum(total_amount) over () as share_of_revenue

from totals
