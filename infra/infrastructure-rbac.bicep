targetScope = 'resourceGroup'

@description('Extend the existing deployment identity; run separately as a role-assignment administrator.')
param identityName string = 'id-projplan-github-deploy'
param accountName string = 'ai-projplan-axowf3'

@description('Existing human administrator targeted by the two Foundry assignments in main.bicep.')
@minLength(36)
@maxLength(36)
param accessPrincipalId string

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' existing = {
  name: identityName
}

resource account 'Microsoft.CognitiveServices/accounts@2026-03-01' existing = {
  name: accountName
}

var rbacAdministratorRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'f58310d9-a9f6-439a-9e8d-f62e7b41a168')
var allowedRoles = '{53ca6127-db72-4b80-b1b0-d745d6d5456d, eadc314b-1a2d-4efa-be10-5d325db5065e}'
var delegationCondition = join([
  '((!(ActionMatches{\'Microsoft.Authorization/roleAssignments/write\'})) OR (@Request[Microsoft.Authorization/roleAssignments:RoleDefinitionId] ForAnyOfAnyValues:GuidEquals ${allowedRoles} AND @Request[Microsoft.Authorization/roleAssignments:PrincipalId] ForAnyOfAnyValues:GuidEquals {${accessPrincipalId}} AND @Request[Microsoft.Authorization/roleAssignments:PrincipalType] ForAnyOfAnyValues:StringEqualsIgnoreCase {\'User\'}))'
  'AND'
  '((!(ActionMatches{\'Microsoft.Authorization/roleAssignments/delete\'})) OR (@Resource[Microsoft.Authorization/roleAssignments:RoleDefinitionId] ForAnyOfAnyValues:GuidEquals ${allowedRoles} AND @Resource[Microsoft.Authorization/roleAssignments:PrincipalId] ForAnyOfAnyValues:GuidEquals {${accessPrincipalId}} AND @Resource[Microsoft.Authorization/roleAssignments:PrincipalType] ForAnyOfAnyValues:StringEqualsIgnoreCase {\'User\'}))'
], ' ')

resource delegatedRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(account.id, identity.id, rbacAdministratorRole)
  scope: account
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: rbacAdministratorRole
    conditionVersion: '2.0'
    condition: delegationCondition
  }
}

output principalId string = identity.properties.principalId
output assignmentScope string = account.id
output condition string = delegationCondition
