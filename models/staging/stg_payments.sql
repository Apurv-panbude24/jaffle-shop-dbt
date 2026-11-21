with source as (
    
    {#- dev: seeds; prod: Azure landing tables (see macros/raw_table.sql) -#}
    select * from {{ raw_table('raw_payments') }}

),

renamed as (

    select
        id as payment_id,
        order_id,
        payment_method,

        -- `amount` is currently stored in cents, so we convert it to dollars
        cast(amount as decimal(18, 2)) / 100 as amount

    from source

)

select * from renamed
