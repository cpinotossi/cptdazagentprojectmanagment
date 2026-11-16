using 'modules/logic-apps-consumption.bicep'

param baseName = 'projplan'
param location = 'swedencentral'
param configuration = loadJsonContent('../logicapps/ontology-orchestration/deployment.json')
param logAnalyticsWorkspaceResourceId = '/subscriptions/98525264-1eb4-493f-983d-16a330caa7f6/resourceGroups/rg-projplan-demo/providers/Microsoft.OperationalInsights/workspaces/log-projplan-axowf3'
param workflowState = 'Disabled'
