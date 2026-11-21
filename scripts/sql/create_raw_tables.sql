-- Landing tables for the Data Factory copy. Column names mirror the seed CSV headers
-- so the copy activity maps by name; dbt's staging models rename them downstream.
-- Run once after the Azure deployment (see infra/azure/README.md).

IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = 'raw')
    EXEC('CREATE SCHEMA raw');
GO

IF OBJECT_ID('raw.raw_customers') IS NULL
    CREATE TABLE raw.raw_customers (
        id          INT          NOT NULL,
        first_name  VARCHAR(100) NULL,
        last_name   VARCHAR(100) NULL
    );
GO

IF OBJECT_ID('raw.raw_orders') IS NULL
    CREATE TABLE raw.raw_orders (
        id          INT          NOT NULL,
        user_id     INT          NOT NULL,
        order_date  DATE         NOT NULL,
        status      VARCHAR(30)  NOT NULL
    );
GO

IF OBJECT_ID('raw.raw_payments') IS NULL
    CREATE TABLE raw.raw_payments (
        id              INT         NOT NULL,
        order_id        INT         NOT NULL,
        payment_method  VARCHAR(30) NOT NULL,
        amount          INT         NOT NULL  -- cents, as in the source
    );
GO
