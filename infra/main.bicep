@description('Primary Azure region for AP application resources.')
param location string = 'southcentralus'

@description('Azure region for the Logic App resource.')
param logicAppLocation string = 'northcentralus'

@description('Azure region for the Static Web App resource.')
@allowed([
  'centralus'
  'eastasia'
  'eastus2'
  'westeurope'
  'westus2'
])
param staticWebAppLocation string = 'centralus'

@description('Environment label written to tags and app settings.')
param environmentName string

@description('Name of the Azure Static Web App.')
param staticWebAppName string

@description('Name of the Azure Functions Flex Consumption App Service plan.')
param appServicePlanName string

@description('Name of the Azure Function App.')
param functionAppName string

@description('Microsoft Entra application client id used by App Service Authentication for the Function API.')
param functionAuthClientId string

@description('Allowed token audience for authenticated calls to the Function API.')
param functionAuthAllowedAudience string

@description('Name of the user-assigned managed identity for the Function App.')
param functionIdentityName string

@description('Name of the Application Insights resource.')
param appInsightsName string

@description('Name of the Log Analytics workspace.')
param logAnalyticsWorkspaceName string

@description('Name of the Storage Account used by the Function runtime/deployment package.')
param functionStorageAccountName string

@description('Name of the Storage Account used for AP artifacts.')
param artifactStorageAccountName string

@description('Name of the existing Key Vault used by this environment.')
param keyVaultName string

@description('Name of the Azure Database for PostgreSQL Flexible Server.')
param postgresServerName string

@description('Name of the Logic App workflow.')
param logicAppName string

@description('Name of the Microsoft Foundry / Azure AI Services account.')
param foundryAccountName string

@description('Name of the default Microsoft Foundry project.')
param foundryProjectName string = 'proj-default'

@description('Microsoft Entra object id for the initial PostgreSQL administrator.')
param entraAdminObjectId string

@description('Microsoft Entra display name for the initial PostgreSQL administrator.')
param entraAdminName string

@description('Azure OpenAI deployment name used by extraction.')
param azureOpenAiDeployment string

@description('Azure OpenAI API version.')
param azureOpenAiApiVersion string = '2024-10-21'

@description('Azure Document Intelligence endpoint. Use the Foundry/Cognitive Services endpoint if Document Intelligence is deployed there.')
param documentIntelligenceEndpoint string

@description('Graph mailbox user principal name for the AP intake mailbox.')
param mailboxUserPrincipalName string

@description('Microsoft Teams team name used for Properties AP escalation notifications.')
param teamsTeamNamePropertiesAp string

@description('Microsoft Teams channel name used for Properties AP escalation notifications.')
param teamsChannelNamePropertiesAp string

@description('Logic App polling interval in seconds.')
@minValue(30)
param intakePollingIntervalSeconds int = 30

@description('Maximum concurrent Logic App intake runs.')
@minValue(1)
param intakeConcurrencyRuns int = 1

@description('Enable timer-triggered Graph intake processing.')
param processGraphIntake bool = true

@description('PostgreSQL SKU name.')
param postgresSkuName string = 'Standard_B1ms'

@description('PostgreSQL SKU tier.')
param postgresSkuTier string = 'Burstable'

@description('PostgreSQL storage size in GB.')
param postgresStorageSizeGB int = 32

@description('Tags applied to resources created by this template.')
param tags object = {}

var commonTags = union(tags, {
  environment: environmentName
  workload: 'properties-ap-mail'
})
var deploymentContainerName = 'app-package'
var artifactContainerName = 'ap-artifacts'
var storageBlobDataContributorRoleId = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
var storageQueueDataContributorRoleId = '974c5e8b-45b9-4653-ba55-5f855dd0fb88'
var storageTableDataContributorRoleId = '0a9a7e1f-b9d0-4cc4-a60d-0319b160aaa3'
var keyVaultSecretsUserRoleId = '4633458b-17de-408a-b874-0445c86b69e6'
var monitoringMetricsPublisherRoleId = '3913510d-42f4-4e42-8a64-420c390055eb'
var cognitiveServicesUserRoleId = 'a97b65f3-24c7-4388-baec-2e87135dc908'
var functionInvokeUrl = 'https://${functionApp.properties.defaultHostName}/api/process-graph-intake'
var foundryEndpoint = 'https://${foundryAccountName}.cognitiveservices.azure.com'

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: logAnalyticsWorkspaceName
  location: location
  tags: commonTags
  properties: {
    retentionInDays: 30
    sku: {
      name: 'PerGB2018'
    }
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: appInsightsName
  location: location
  kind: 'web'
  tags: commonTags
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logAnalytics.id
    DisableLocalAuth: true
  }
}

resource functionStorage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: functionStorageAccountName
  location: location
  kind: 'StorageV2'
  tags: commonTags
  sku: {
    name: 'Standard_LRS'
  }
  properties: {
    accessTier: 'Hot'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    minimumTlsVersion: 'TLS1_2'
    publicNetworkAccess: 'Enabled'
  }
}

resource functionBlobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: functionStorage
  name: 'default'
}

resource deploymentContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: functionBlobService
  name: deploymentContainerName
  properties: {
    publicAccess: 'None'
  }
}

resource artifactStorage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: artifactStorageAccountName
  location: location
  kind: 'StorageV2'
  tags: commonTags
  sku: {
    name: 'Standard_LRS'
  }
  properties: {
    accessTier: 'Hot'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    minimumTlsVersion: 'TLS1_2'
    publicNetworkAccess: 'Enabled'
  }
}

resource artifactBlobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: artifactStorage
  name: 'default'
}

resource artifactContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: artifactBlobService
  name: artifactContainerName
  properties: {
    publicAccess: 'None'
  }
}

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: functionIdentityName
  location: location
  tags: commonTags
}

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
}

resource foundry 'Microsoft.CognitiveServices/accounts@2025-09-01' = {
  name: foundryAccountName
  location: location
  kind: 'AIServices'
  tags: commonTags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identity.id}': {}
    }
  }
  sku: {
    name: 'S0'
  }
  properties: {
    allowProjectManagement: true
    associatedProjects: [
      foundryProjectName
    ]
    customSubDomainName: foundryAccountName
    defaultProject: foundryProjectName
    disableLocalAuth: true
    networkAcls: {
      defaultAction: 'Allow'
      ipRules: []
      virtualNetworkRules: []
    }
    publicNetworkAccess: 'Enabled'
  }
}

resource foundryProject 'Microsoft.CognitiveServices/accounts/projects@2025-09-01' = {
  parent: foundry
  name: foundryProjectName
  location: location
  tags: commonTags
  properties: {
    description: 'Default AP Automation Foundry project.'
    displayName: foundryProjectName
  }
}

resource postgres 'Microsoft.DBforPostgreSQL/flexibleServers@2023-06-01-preview' = {
  name: postgresServerName
  location: location
  tags: commonTags
  sku: {
    name: postgresSkuName
    tier: postgresSkuTier
  }
  properties: {
    version: '16'
    storage: {
      storageSizeGB: postgresStorageSizeGB
    }
    authConfig: {
      activeDirectoryAuth: 'Enabled'
      passwordAuth: 'Disabled'
      tenantId: tenant().tenantId
    }
  }
}

resource postgresAdmin 'Microsoft.DBforPostgreSQL/flexibleServers/administrators@2023-06-01-preview' = {
  parent: postgres
  name: entraAdminObjectId
  properties: {
    principalName: entraAdminName
    principalType: 'User'
    tenantId: tenant().tenantId
  }
}

resource plan 'Microsoft.Web/serverfarms@2024-04-01' = {
  name: appServicePlanName
  location: location
  kind: 'functionapp'
  tags: commonTags
  sku: {
    tier: 'FlexConsumption'
    name: 'FC1'
  }
  properties: {
    reserved: true
  }
}

resource functionApp 'Microsoft.Web/sites@2024-04-01' = {
  name: functionAppName
  location: location
  kind: 'functionapp,linux'
  tags: commonTags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identity.id}': {}
    }
  }
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    keyVaultReferenceIdentity: identity.id
    siteConfig: {
      minTlsVersion: '1.2'
    }
    functionAppConfig: {
      deployment: {
        storage: {
          type: 'blobContainer'
          value: '${functionStorage.properties.primaryEndpoints.blob}${deploymentContainerName}'
          authentication: {
            type: 'UserAssignedIdentity'
            userAssignedIdentityResourceId: identity.id
          }
        }
      }
      scaleAndConcurrency: {
        maximumInstanceCount: 100
        instanceMemoryMB: 2048
      }
      runtime: {
        name: 'python'
        version: '3.11'
      }
    }
  }
  resource appSettings 'config' = {
    name: 'appsettings'
    properties: {
      APP_ENV: 'AZURE'
      AZURE_CLIENT_ID: identity.properties.clientId
      AzureWebJobsStorage__accountName: functionStorage.name
      AzureWebJobsStorage__credential: 'managedidentity'
      AzureWebJobsStorage__clientId: identity.properties.clientId
      APPLICATIONINSIGHTS_CONNECTION_STRING: appInsights.properties.ConnectionString
      APPLICATIONINSIGHTS_AUTHENTICATION_STRING: 'ClientId=${identity.properties.clientId};Authorization=AAD'
      AZURE_STORAGE_ACCOUNT_NAME: artifactStorage.name
      AP_ARTIFACT_CONTAINER: artifactContainerName
      DATABASE_URL: 'host=${postgres.properties.fullyQualifiedDomainName} dbname=apautomation user=${identity.name} sslmode=require'
      AZURE_OPENAI_ENDPOINT: foundryEndpoint
      AZURE_OPENAI_DEPLOYMENT: azureOpenAiDeployment
      AZURE_OPENAI_API_VERSION: azureOpenAiApiVersion
      AZURE_OPENAI_AUTH_MODE: 'identity'
      AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT: documentIntelligenceEndpoint
      AZURE_DOCUMENT_INTELLIGENCE_AUTH_MODE: 'identity'
      GRAPH_AUTH_MODE: 'client_secret'
      AZURE_CLIENT_ID_MAIL: '@Microsoft.KeyVault(VaultName=${keyVault.name};SecretName=AZURE-CLIENT-ID-MAIL)'
      AZURE_TENANT_ID: '@Microsoft.KeyVault(VaultName=${keyVault.name};SecretName=AZURE-TENANT-ID)'
      AZURE_CLIENT_SECRET_MAIL: '@Microsoft.KeyVault(VaultName=${keyVault.name};SecretName=AZURE-CLIENT-SECRET-MAIL)'
      USER_PRINCIPAL_NAME_MAIL: mailboxUserPrincipalName
      TEAMS_WEBHOOK_URL_PROPERTIES_AP: '@Microsoft.KeyVault(VaultName=${keyVault.name};SecretName=TEAMS-WEBHOOK-URL-PROPERTIES-AP)'
      TEAMS_TEAM_NAME_PROPERTIES_AP: teamsTeamNamePropertiesAp
      TEAMS_CHANNEL_NAME_PROPERTIES_AP: teamsChannelNamePropertiesAp
      AP_PROCESS_GRAPH_INTAKE: string(processGraphIntake)
      KEY_VAULT_NAME: keyVault.name
      LOGIC_APP_PRINCIPAL_ID: logicApp.identity.principalId
      FUNCTION_AUTH_ALLOWED_AUDIENCE: functionAuthAllowedAudience
    }
  }
}

resource staticWebApp 'Microsoft.Web/staticSites@2023-12-01' = {
  name: staticWebAppName
  location: staticWebAppLocation
  tags: commonTags
  sku: {
    name: 'Standard'
    tier: 'Standard'
  }
  properties: {
    allowConfigFileUpdates: true
  }
}

resource staticWebAppFunctionBackend 'Microsoft.Web/staticSites/linkedBackends@2023-12-01' = {
  parent: staticWebApp
  name: 'functionApp'
  properties: {
    backendResourceId: functionApp.id
    region: location
  }
}

resource logicApp 'Microsoft.Logic/workflows@2019-05-01' = {
  name: logicAppName
  location: logicAppLocation
  tags: commonTags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    state: processGraphIntake ? 'Enabled' : 'Disabled'
    definition: {
      '$schema': 'https://schema.management.azure.com/providers/Microsoft.Logic/schemas/2016-06-01/workflowdefinition.json#'
      contentVersion: '1.0.0.0'
      parameters: {}
      triggers: {
        every_30_seconds: {
          type: 'Recurrence'
          recurrence: {
            frequency: 'Second'
            interval: intakePollingIntervalSeconds
          }
          evaluatedRecurrence: {
            frequency: 'Second'
            interval: intakePollingIntervalSeconds
          }
          runtimeConfiguration: {
            concurrency: {
              runs: intakeConcurrencyRuns
            }
          }
        }
      }
      actions: {
        call_function_intake_processor: {
          type: 'Http'
          inputs: {
            method: 'POST'
            uri: functionInvokeUrl
            authentication: {
              type: 'ManagedServiceIdentity'
              audience: functionAuthAllowedAudience
            }
          }
          runAfter: {}
        }
      }
      outputs: {}
    }
    parameters: {}
  }
}

resource functionAuthSettings 'Microsoft.Web/sites/config@2024-04-01' = {
  parent: functionApp
  name: 'authsettingsV2'
  properties: {
    platform: {
      enabled: true
      runtimeVersion: '~1'
    }
    globalValidation: {
      requireAuthentication: true
      unauthenticatedClientAction: 'Return401'
    }
    identityProviders: {
      azureActiveDirectory: {
        enabled: true
        registration: {
          clientId: functionAuthClientId
          openIdIssuer: 'https://login.microsoftonline.com/${tenant().tenantId}/v2.0'
        }
        validation: {
          allowedAudiences: [
            functionAuthAllowedAudience
          ]
        }
      }
    }
  }
}

resource functionStorageBlobRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(functionStorage.id, identity.id, storageBlobDataContributorRoleId)
  scope: functionStorage
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageBlobDataContributorRoleId)
  }
}

resource functionStorageQueueRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(functionStorage.id, identity.id, storageQueueDataContributorRoleId)
  scope: functionStorage
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageQueueDataContributorRoleId)
  }
}

resource functionStorageTableRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(functionStorage.id, identity.id, storageTableDataContributorRoleId)
  scope: functionStorage
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageTableDataContributorRoleId)
  }
}

resource artifactStorageBlobRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(artifactStorage.id, identity.id, storageBlobDataContributorRoleId)
  scope: artifactStorage
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageBlobDataContributorRoleId)
  }
}

resource keyVaultRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, identity.id, keyVaultSecretsUserRoleId)
  scope: keyVault
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', keyVaultSecretsUserRoleId)
  }
}

resource foundryRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(foundry.id, identity.id, cognitiveServicesUserRoleId)
  scope: foundry
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cognitiveServicesUserRoleId)
  }
}

resource appInsightsRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(appInsights.id, identity.id, monitoringMetricsPublisherRoleId)
  scope: appInsights
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', monitoringMetricsPublisherRoleId)
  }
}

output functionAppName string = functionApp.name
output functionIdentityName string = identity.name
output functionIdentityPrincipalId string = identity.properties.principalId
output staticWebAppName string = staticWebApp.name
output functionStorageAccountName string = functionStorage.name
output artifactStorageAccountName string = artifactStorage.name
output postgresServerName string = postgres.name
output keyVaultName string = keyVault.name
output foundryAccountName string = foundry.name
output foundryProjectName string = foundryProject.name
output logicAppName string = logicApp.name
