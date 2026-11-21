with source as (

    {#- dev: seeds; prod: Azure landing tables (see macros/raw_table.sql) -#}
    select * from {{ raw_table('raw_customers') }}

),

renamed as (

    select
        id as customer_id,
        first_name,
        last_name

    from source

)

select * from renamed
