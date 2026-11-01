@description('Kurzer Name, wird in Ressourcennamen verwendet')
@minLength(3)
@maxLength(12)
param baseName string = 'projplan'

@description('Region mit Foundry und vollem Fabric-Angebot')
param location string = resourceGroup().location

@description('Object-ID des Deployers (az ad signed-in-user show --query id)')
param principalId string

@description('UPN des Deployers, wird Fabric-Kapazitätsadmin')
param fabricAdminUpn string

param fabricSku string = 'F2'
param modelName string = 'gpt-4.1'
param modelVersion string = '2025-04-14'
param modelCapacity int = 50

var suffix = take(uniqueString(resourceGroup().id), 6)
var accountName = 'ai-${baseName}-${suffix}'
var foundryUserRole = '53ca6127-db72-4b80-b1b0-d745d6d5456d'
var foundryProjectManagerRole = 'eadc314b-1a2d-4efa-be10-5d325db5065e'

resource account 'Microsoft.CognitiveServices/accounts@2026-03-01' = {
  name: accountName
  location: location
  kind: 'AIServices'
  sku: { name: 'S0' }
  identity: { type: 'SystemAssigned' }
  properties: {
    allowProjectManagement: true
    customSubDomainName: accountName
    publicNetworkAccess: 'Enabled'
    disableLocalAuth: true
  }
}

resource model 'Microsoft.CognitiveServices/accounts/deployments@2026-03-01' = {
  parent: account
  name: modelName
  sku: { name: 'GlobalStandard', capacity: modelCapacity }
  properties: {
    model: { format: 'OpenAI', name: modelName, version: modelVersion }
  }
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2026-03-01' = {
  parent: account
  name: 'projektplanung'
  location: location
  dependsOn: [model] // Operationen am selben Account dürfen nicht parallel laufen
  identity: { type: 'SystemAssigned' }
  properties: {
    displayName: 'Projektplanung'
  }
}

resource capacity 'Microsoft.Fabric/capacities@2023-11-01' = {
  name: 'fc${baseName}${suffix}'
  location: location
  sku: { name: fabricSku, tier: 'Fabric' }
  properties: {
    administration: { members: [fabricAdminUpn] }
  }
}

resource deployerFoundryUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: account
  name: guid(account.id, principalId, foundryUserRole)
  properties: {
    principalId: principalId
    principalType: 'User'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', foundryUserRole)
  }
}

resource deployerFoundryManager 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: account
  name: guid(account.id, principalId, foundryProjectManagerRole)
  properties: {
    principalId: principalId
    principalType: 'User'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', foundryProjectManagerRole)
  }
}

output foundryAccountName string = account.name
output foundryProjectName string = project.name
output foundryProjectEndpoint string = 'https://${account.name}.services.ai.azure.com/api/projects/${project.name}'
output modelDeployment string = model.name
output fabricCapacityName string = capacity.name
