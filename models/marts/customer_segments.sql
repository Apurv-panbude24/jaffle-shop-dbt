{#-
    Segments customers by lifetime value and order frequency.
    Thresholds live in dbt_project.yml (vars) so they can be tuned without
    touching SQL.
-#}

with customers as (

    select * from {{ ref('customers') }}

),

segmented as (

    select
        customer_id,
        first_name,
        last_name,
        first_order,
        most_recent_order,
        number_of_orders,
        coalesce(customer_lifetime_value, 0) as customer_lifetime_value,

        case
            when number_of_orders is null then 'no_orders'
            when coalesce(customer_lifetime_value, 0) >= {{ var('segment_high_ltv') }} then 'high_value'
            when coalesce(customer_lifetime_value, 0) >= {{ var('segment_medium_ltv') }} then 'medium_value'
            else 'low_value'
        end as value_segment,

        coalesce(number_of_orders, 0) > 1 as is_repeat_customer

    from customers

)

select * from segmented
