targetScope = 'resourceGroup'

@description('Short prefix used for the Consumption workflow name.')
@minLength(3)
@maxLength(12)
param baseName string = 'projplan'

param location string = resourceGroup().location

@description('Optional workspace resource ID for workflow runtime diagnostics.')
param logAnalyticsWorkspaceResourceId string = ''

var suffix = take(uniqueString(resourceGroup().id, baseName, 'logic-apps'), 6)

resource workflow 'Microsoft.Logic/workflows@2019-05-01' = {
  name: 'logic-${baseName}-${suffix}'
  location: location
  tags: {
    component: 'ontology-orchestration-preparation'
    readiness: 'integration-not-configured'
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    state: 'Disabled'
    definition: loadJsonContent('../../logicapps/ontology-orchestration/workflow.json').definition
    parameters: {}
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
output logicAppPrincipalId string = workflow.identity.principalId
output workflowState string = 'Disabled'
