using 'main.bicep'

param baseName = 'projplan'
param location = 'swedencentral'
param principalId = readEnvironmentVariable('FOUNDRY_ACCESS_PRINCIPAL_ID')
param fabricAdminUpn = readEnvironmentVariable('FABRIC_ADMIN_UPN')
param fabricSku = 'F4'
param modelName = 'gpt-4.1'
param modelVersion = '2025-04-14'
param modelCapacity = 50
param embeddingName = 'text-embedding-3-small'
param embeddingVersion = '1'
param embeddingCapacity = 20
param deployLogicApp = true
param logicAppConfiguration = loadJsonContent('../logicapps/ontology-orchestration/deployment.json')
