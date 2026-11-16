targetScope = 'resourceGroup'

@description('Short prefix used for the Consumption workflow name.')
@minLength(3)
@maxLength(12)
param baseName string = 'projplan'

param location string = resourceGroup().location

@description('Optional workspace resource ID for workflow runtime diagnostics.')
param logAnalyticsWorkspaceResourceId string = ''

@description('Nonsecret environment bindings for Fabric IQ and Foundry.')
param configuration object = {}

@allowed(['Disabled', 'Enabled'])
@description('Enable explicitly for manual tests with delegated Fabric authentication.')
param workflowState string = 'Disabled'

var suffix = take(uniqueString(resourceGroup().id, baseName, 'logic-apps'), 6)
var runtimeIdentityResourceId = configuration.?runtimeIdentityResourceId ?? ''
var workspaceId = configuration.?workspaceId ?? ''
var ontologyId = configuration.?ontologyId ?? ''
var foundryProjectEndpoint = configuration.?foundryProjectEndpoint ?? ''
var callerObjectIds = configuration.?allowedCallerObjectIds ?? []
var prompts = loadJsonContent('../../logicapps/ontology-orchestration/prompts.json')

resource runtimeIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: empty(runtimeIdentityResourceId) ? 'unconfigured' : last(split(runtimeIdentityResourceId, '/'))
}

resource workflow 'Microsoft.Logic/workflows@2019-05-01' = {
  name: 'logic-${baseName}-${suffix}'
  location: location
  tags: {
    component: 'ontology-orchestration'
    readiness: 'delegated-auth-and-manual-validation-required'
  }
  identity: empty(runtimeIdentityResourceId) ? {
    type: 'None'
  } : {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${runtimeIdentityResourceId}': {}
    }
  }
  properties: {
    state: workflowState
    definition: loadJsonContent('../../logicapps/ontology-orchestration/workflow.json').definition
    // sasAuthenticationPolicy is supported by the service but absent from bundled Bicep types.
    accessControl: {
      triggers: union({
        openAuthenticationPolicies: {
          policies: toObject(empty(callerObjectIds) ? ['00000000-0000-0000-0000-000000000000'] : callerObjectIds, caller => caller, caller => {
            type: 'AAD'
            claims: [
              { name: 'iss', value: 'https://sts.windows.net/${tenant().tenantId}/' }
              { name: 'aud', value: environment().resourceManager }
              { name: 'tid', value: tenant().tenantId }
              { name: 'oid', value: caller }
            ]
          })
        }
      }, {
        sasAuthenticationPolicy: { state: 'Disabled' }
      })
    }
    parameters: {
      runtimeIdentityResourceId: { value: runtimeIdentityResourceId }
      workspaceId: { value: workspaceId }
      ontologyId: { value: ontologyId }
      foundryProjectEndpoint: { value: foundryProjectEndpoint }
      modelDeployment: { value: configuration.?modelDeployment ?? loadJsonContent('../../agents/fabric-iq/config.json').model }
      rewritePrompt: { value: prompts.rewrite }
      validationPrompt: { value: prompts.validation }
    }
  }
}

resource diagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = if (!empty(logAnalyticsWorkspaceResourceId)) {
  name: 'workflow-runtime'
  scope: workflow
  properties: {
    workspaceId: logAnalyticsWorkspaceResourceId
    logs: [
      {
        category: 'WorkflowRuntime'
        enabled: true
      }
    ]
  }
}

output logicAppName string = workflow.name
output logicAppResourceId string = workflow.id
output logicAppPrincipalId string = empty(runtimeIdentityResourceId) ? '' : runtimeIdentity.properties.principalId
output deployedWorkflowState string = workflowState
