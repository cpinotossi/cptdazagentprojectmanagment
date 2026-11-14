targetScope = 'resourceGroup'

@description('Bootstrap separately using an administrator with role-assignment permissions.')
param identityName string = 'id-projplan-github-foundry'

param location string = resourceGroup().location
param accountName string = 'ai-projplan-axowf3'
param projectName string = 'project-planning'

@description('Repository component of the OIDC subject, including immutable owner and repository IDs.')
param githubRepository string = 'cpinotossi@8876980/cptdazagentprojectmanagment@1442137267'
param githubEnvironment string = 'foundry-demo'

var foundryUserRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '53ca6127-db72-4b80-b1b0-d745d6d5456d')

resource account 'Microsoft.CognitiveServices/accounts@2026-03-01' existing = {
  name: accountName
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2026-03-01' existing = {
  parent: account
  name: projectName
}

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' = {
  name: identityName
  location: location
  tags: {
    component: 'github-foundry-release-identity'
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

resource projectRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(project.id, identity.id, foundryUserRole)
  scope: project
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: foundryUserRole
  }
}

output clientId string = identity.properties.clientId
output principalId string = identity.properties.principalId
output tenantId string = tenant().tenantId
output subscriptionId string = subscription().subscriptionId
output projectScope string = project.id
output federationSubject string = federation.properties.subject
