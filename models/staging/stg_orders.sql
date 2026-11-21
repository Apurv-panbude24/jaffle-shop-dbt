with source as (

    {#- dev: seeds; prod: Azure landing tables (see macros/raw_table.sql) -#}
    select * from {{ raw_table('raw_orders') }}

),

renamed as (

    select
        id as order_id,
        user_id as customer_id,
        order_date,
        status

    from source

)

select * from renamed
