#!/usr/bin/env bash
# Deploys the Azure resources with Bicep, then sets up Fabric and the agent.
set -euo pipefail
cd "$(dirname "$0")"

LOCATION="${LOCATION:-swedencentral}"
export AZURE_RESOURCE_GROUP="${AZURE_RESOURCE_GROUP:-rg-projplan-demo}"
export AZURE_SUBSCRIPTION_ID="$(az account show --query id -o tsv)"

az group create -n "$AZURE_RESOURCE_GROUP" -l "$LOCATION" -o none

out() { az deployment group show -g "$AZURE_RESOURCE_GROUP" -n main --query "properties.outputs.$1.value" -o tsv; }

az deployment group create -g "$AZURE_RESOURCE_GROUP" -n main -o none \
  --template-file infra/main.bicep \
  --parameters location="$LOCATION" \
               principalId="$(az ad signed-in-user show --query id -o tsv)" \
               fabricAdminUpn="$(az ad signed-in-user show --query userPrincipalName -o tsv)" \
               fabricSku="${FABRIC_SKU:-F2}"

export FOUNDRY_ACCOUNT_NAME="$(out foundryAccountName)"
export FOUNDRY_PROJECT_NAME="$(out foundryProjectName)"
export FOUNDRY_PROJECT_ENDPOINT="$(out foundryProjectEndpoint)"
export FOUNDRY_MODEL_NAME="$(out modelDeployment)"
export FOUNDRY_EMBEDDING_NAME="$(out embeddingDeployment)"
export FABRIC_CAPACITY_NAME="$(out fabricCapacityName)"

[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q -r scripts/requirements.txt
.venv/bin/python scripts/setup.py
