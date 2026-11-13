targetScope = 'resourceGroup'

@description('Bootstrap separately using an administrator with role-assignment permissions.')
param identityName string = 'id-projplan-github-deploy'

param location string = resourceGroup().location
param githubRepository string = 'cpinotossi/cptdazagentprojectmanagment'
param githubEnvironment string = 'azure-demo'

var contributorRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'b24988ac-6180-42a0-ab88-20f7382dd24c')

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' = {
  name: identityName
  location: location
  tags: {
    component: 'github-deployment-identity'
  }
}

resource federation 'Microsoft.ManagedIdentity/userAssignedIdentities/federatedIdentityCredentials@2024-11-30' = {
  parent: identity
  name: 'github-environment'
  properties: {
    issuer: 'https://token.actions.githubusercontent.com'
    subject: 'repo:${githubRepository}:environment:${githubEnvironment}'
    audiences: [
      'api://AzureADTokenExchange'
    ]
  }
}

resource deploymentRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(resourceGroup().id, identity.id, contributorRole)
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: contributorRole
  }
}

output clientId string = identity.properties.clientId
output principalId string = identity.properties.principalId
output tenantId string = tenant().tenantId
output subscriptionId string = subscription().subscriptionId
output resourceGroupName string = resourceGroup().name
output federationSubject string = federation.properties.subject
