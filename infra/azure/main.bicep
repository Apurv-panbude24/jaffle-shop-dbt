// Azure leg of the jaffle-shop pipeline:
//   GitHub raw CSVs -> Data Factory -> ADLS Gen2 (raw) -> Azure SQL Database -> dbt
// Everything is created from this file and removed with teardown.sh.

targetScope = 'resourceGroup'

@description('Azure region. Pick one close to you with Azure SQL free-offer capacity.')
param location string = resourceGroup().location

@description('Short lowercase prefix for resource names (3-8 chars).')
@minLength(3)
@maxLength(8)
param namePrefix string = 'jaffle'

@description('SQL administrator login.')
param sqlAdminLogin string = 'jaffleadmin'

@description('SQL administrator password. Passed at deploy time, never committed.')
@secure()
param sqlAdminPassword string

@description('Email for budget alerts.')
param alertEmail string

@description('Monthly budget in the subscription currency. Alerts at 50%, 90% and 100%.')
param monthlyBudget int = 10

@description('First day of the current month; required by the budget API.')
param budgetStartDate string = utcNow('yyyy-MM-01')

@description('Your public IP, so you can reach the SQL server from your laptop (leave empty to skip).')
param clientIp string = ''

@description('Public repo + branch the seed CSVs are read from.')
param seedBaseUrl string = 'https://raw.githubusercontent.com/Apurv-panbude24/jaffle-shop-dbt/main/seeds/'

var suffix = uniqueString(resourceGroup().id)
var storageName = take('st${namePrefix}${suffix}', 24)
var sqlServerName = 'sql-${namePrefix}-${suffix}'
var sqlDbName = 'jaffle_shop'
var factoryName = 'adf-${namePrefix}-${suffix}'
var tables = [
  'raw_customers'
  'raw_orders'
  'raw_payments'
]

// ---------- ADLS Gen2 (raw landing zone) ----------
resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageName
  location: location
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    isHnsEnabled: true // hierarchical namespace = ADLS Gen2
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    supportsHttpsTrafficOnly: true
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
}

resource rawContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: 'raw'
}

// ---------- Azure SQL Database (free serverless offer) ----------
resource sqlServer 'Microsoft.Sql/servers@2023-08-01-preview' = {
  name: sqlServerName
  location: location
  properties: {
    administratorLogin: sqlAdminLogin
    administratorLoginPassword: sqlAdminPassword
    minimalTlsVersion: '1.2'
    publicNetworkAccess: 'Enabled'
  }
}

resource sqlDb 'Microsoft.Sql/servers/databases@2023-08-01-preview' = {
  parent: sqlServer
  name: sqlDbName
  location: location
  sku: {
    name: 'GP_S_Gen5'
    tier: 'GeneralPurpose'
    family: 'Gen5'
    capacity: 1
  }
  properties: {
    useFreeLimit: true
    freeLimitExhaustionBehavior: 'AutoPause' // pause instead of billing when the free allowance runs out
    autoPauseDelay: 60
    minCapacity: json('0.5')
    maxSizeBytes: 34359738368 // 32 GB
    requestedBackupStorageRedundancy: 'Local'
  }
}

// Lets Data Factory (an Azure service) reach the server.
resource allowAzure 'Microsoft.Sql/servers/firewallRules@2023-08-01-preview' = {
  parent: sqlServer
  name: 'AllowAzureServices'
  properties: {
    startIpAddress: '0.0.0.0'
    endIpAddress: '0.0.0.0'
  }
}

resource allowClient 'Microsoft.Sql/servers/firewallRules@2023-08-01-preview' = if (!empty(clientIp)) {
  parent: sqlServer
  name: 'AllowClient'
  properties: {
    startIpAddress: clientIp
    endIpAddress: clientIp
  }
}

// ---------- Data Factory ----------
resource factory 'Microsoft.DataFactory/factories@2018-06-01' = {
  name: factoryName
  location: location
  identity: { type: 'SystemAssigned' }
}

// The factory's managed identity may write to the raw container; no storage keys anywhere.
var storageBlobDataContributor = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
resource factoryCanWriteStorage 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, factory.id, storageBlobDataContributor)
  scope: storage
  properties: {
    principalId: factory.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageBlobDataContributor)
  }
}

resource lsHttp 'Microsoft.DataFactory/factories/linkedservices@2018-06-01' = {
  parent: factory
  name: 'ls_github_seeds'
  properties: {
    type: 'HttpServer'
    typeProperties: {
      url: seedBaseUrl
      authenticationType: 'Anonymous'
    }
  }
}

resource lsAdls 'Microsoft.DataFactory/factories/linkedservices@2018-06-01' = {
  parent: factory
  name: 'ls_adls_raw'
  properties: {
    type: 'AzureBlobFS'
    typeProperties: {
      url: storage.properties.primaryEndpoints.dfs // auth = factory managed identity
    }
  }
}

resource lsSql 'Microsoft.DataFactory/factories/linkedservices@2018-06-01' = {
  parent: factory
  name: 'ls_azure_sql'
  properties: {
    type: 'AzureSqlDatabase'
    typeProperties: {
      connectionString: 'Server=tcp:${sqlServer.properties.fullyQualifiedDomainName},1433;Database=${sqlDbName};User ID=${sqlAdminLogin};Encrypt=True;TrustServerCertificate=False;Connection Timeout=30;'
      password: {
        type: 'SecureString'
        value: sqlAdminPassword
      }
    }
  }
}

resource dsGithubCsv 'Microsoft.DataFactory/factories/datasets@2018-06-01' = {
  parent: factory
  name: 'ds_github_csv'
  properties: {
    type: 'DelimitedText'
    linkedServiceName: { referenceName: lsHttp.name, type: 'LinkedServiceReference' }
    parameters: { fileName: { type: 'String' } }
    typeProperties: {
      location: {
        type: 'HttpServerLocation'
        relativeUrl: { value: '@dataset().fileName', type: 'Expression' }
      }
      columnDelimiter: ','
      firstRowAsHeader: true
    }
  }
}

resource dsAdlsCsv 'Microsoft.DataFactory/factories/datasets@2018-06-01' = {
  parent: factory
  name: 'ds_adls_raw_csv'
  properties: {
    type: 'DelimitedText'
    linkedServiceName: { referenceName: lsAdls.name, type: 'LinkedServiceReference' }
    parameters: { fileName: { type: 'String' } }
    typeProperties: {
      location: {
        type: 'AzureBlobFSLocation'
        fileSystem: 'raw'
        fileName: { value: '@dataset().fileName', type: 'Expression' }
      }
      columnDelimiter: ','
      firstRowAsHeader: true
    }
  }
}

resource dsSqlRaw 'Microsoft.DataFactory/factories/datasets@2018-06-01' = {
  parent: factory
  name: 'ds_sql_raw'
  properties: {
    type: 'AzureSqlTable'
    linkedServiceName: { referenceName: lsSql.name, type: 'LinkedServiceReference' }
    parameters: { tableName: { type: 'String' } }
    typeProperties: {
      schema: 'raw'
      table: { value: '@dataset().tableName', type: 'Expression' }
    }
  }
}

// One pipeline: for each raw table, copy CSV -> ADLS, then ADLS -> Azure SQL (truncate + load).
resource pipeline 'Microsoft.DataFactory/factories/pipelines@2018-06-01' = {
  parent: factory
  name: 'pl_ingest_raw'
  dependsOn: [ dsGithubCsv, dsAdlsCsv, dsSqlRaw, factoryCanWriteStorage, rawContainer ]
  properties: {
    parameters: {
      tables: { type: 'Array', defaultValue: tables }
    }
    activities: [
      {
        name: 'ForEachTable'
        type: 'ForEach'
        typeProperties: {
          items: { value: '@pipeline().parameters.tables', type: 'Expression' }
          isSequential: false
          activities: [
            {
              name: 'CopyGithubToAdls'
              type: 'Copy'
              inputs: [
                {
                  referenceName: 'ds_github_csv'
                  type: 'DatasetReference'
                  parameters: { fileName: { value: '@concat(item(), \'.csv\')', type: 'Expression' } }
                }
              ]
              outputs: [
                {
                  referenceName: 'ds_adls_raw_csv'
                  type: 'DatasetReference'
                  parameters: { fileName: { value: '@concat(item(), \'.csv\')', type: 'Expression' } }
                }
              ]
              typeProperties: {
                source: {
                  type: 'DelimitedTextSource'
                  storeSettings: { type: 'HttpReadSettings', requestMethod: 'GET' }
                  formatSettings: { type: 'DelimitedTextReadSettings' }
                }
                sink: {
                  type: 'DelimitedTextSink'
                  storeSettings: { type: 'AzureBlobFSWriteSettings' }
                  formatSettings: { type: 'DelimitedTextWriteSettings', quoteAllText: true, fileExtension: '.csv' }
                }
              }
            }
            {
              name: 'CopyAdlsToSql'
              type: 'Copy'
              dependsOn: [ { activity: 'CopyGithubToAdls', dependencyConditions: [ 'Succeeded' ] } ]
              inputs: [
                {
                  referenceName: 'ds_adls_raw_csv'
                  type: 'DatasetReference'
                  parameters: { fileName: { value: '@concat(item(), \'.csv\')', type: 'Expression' } }
                }
              ]
              outputs: [
                {
                  referenceName: 'ds_sql_raw'
                  type: 'DatasetReference'
                  parameters: { tableName: { value: '@item()', type: 'Expression' } }
                }
              ]
              typeProperties: {
                source: {
                  type: 'DelimitedTextSource'
                  storeSettings: { type: 'AzureBlobFSReadSettings' }
                  formatSettings: { type: 'DelimitedTextReadSettings' }
                }
                sink: {
                  type: 'AzureSqlSink'
                  writeBehavior: 'insert'
                  preCopyScript: { value: '@concat(\'TRUNCATE TABLE raw.\', item())', type: 'Expression' }
                }
              }
            }
          ]
        }
      }
    ]
  }
}

// Daily at 05:00 UTC, one hour before the dbt nightly build. Created stopped; start with:
//   az datafactory trigger start -g <rg> --factory-name <adf> -n tr_daily
resource trigger 'Microsoft.DataFactory/factories/triggers@2018-06-01' = {
  parent: factory
  name: 'tr_daily'
  dependsOn: [ pipeline ]
  properties: {
    type: 'ScheduleTrigger'
    pipelines: [
      { pipelineReference: { referenceName: 'pl_ingest_raw', type: 'PipelineReference' } }
    ]
    typeProperties: {
      recurrence: {
        frequency: 'Day'
        interval: 1
        startTime: '${budgetStartDate}T05:00:00Z'
        timeZone: 'UTC'
      }
    }
  }
}

// ---------- Cost guard-rail ----------
resource budget 'Microsoft.Consumption/budgets@2023-05-01' = {
  name: 'budget-${namePrefix}'
  properties: {
    category: 'Cost'
    amount: monthlyBudget
    timeGrain: 'Monthly'
    timePeriod: { startDate: budgetStartDate }
    notifications: {
      actual50: {
        enabled: true
        operator: 'GreaterThan'
        threshold: 50
        contactEmails: [ alertEmail ]
      }
      actual90: {
        enabled: true
        operator: 'GreaterThan'
        threshold: 90
        contactEmails: [ alertEmail ]
      }
      forecast100: {
        enabled: true
        operator: 'GreaterThan'
        threshold: 100
        thresholdType: 'Forecasted'
        contactEmails: [ alertEmail ]
      }
    }
  }
}

output storageAccountName string = storage.name
output sqlServerFqdn string = sqlServer.properties.fullyQualifiedDomainName
output sqlDatabaseName string = sqlDb.name
output dataFactoryName string = factory.name
