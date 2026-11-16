#!/usr/bin/env bash
# Deploy only Consumption infrastructure; delegated Fabric authentication is provided per request.
set -euo pipefail
cd "$(dirname "$0")/.."

mode="${1:-check}"
case "$mode" in
  check|what-if|deploy|activate) ;;
  *) printf 'Usage: bash scripts/deploy_logic_app.sh [check|what-if|deploy|activate]\n' >&2; exit 2 ;;
esac
if [[ $# -gt 1 ]]; then
  printf 'Only one mode argument is accepted.\n' >&2
  exit 2
fi

temp_dir="$(mktemp -d)"
trap 'rm -f -- "$temp_dir/template.json" "$temp_dir/parameters.json"; rmdir -- "$temp_dir"' EXIT

template=infra/modules/logic-apps-consumption.bicep
az bicep build --file "$template" --outfile "$temp_dir/template.json"
az bicep build-params --file infra/logic-apps-demo.bicepparam --outfile "$temp_dir/parameters.json"
if [[ "$mode" == check ]]; then
  printf 'Consumption Bicep and demo parameters compiled. No Azure deployment performed.\n'
  exit 0
fi

: "${AZURE_SUBSCRIPTION_ID:?Set the target subscription explicitly}"
: "${LOGIC_APP_RESOURCE_GROUP:?Set the existing target resource group}"
: "${LOGIC_APP_LOCATION:?Choose the Consumption workflow region}"
if [[ "$AZURE_SUBSCRIPTION_ID" != 98525264-1eb4-493f-983d-16a330caa7f6 ||
      "$LOGIC_APP_RESOURCE_GROUP" != rg-projplan-demo ||
      "$LOGIC_APP_LOCATION" != swedencentral ||
      "${LOGIC_APP_BASE_NAME:-projplan}" != projplan ]]; then
  printf 'The parameter file is restricted to the approved demo subscription, resource group, region and base name.\n' >&2
  exit 2
fi
tenant="$(az account show --subscription "$AZURE_SUBSCRIPTION_ID" --query tenantId --output tsv)"
if [[ "$tenant" != f71980b2-590a-4de9-90d5-6fbc867da951 ]]; then
  printf 'The Azure session does not match the approved tenant.\n' >&2
  exit 2
fi
deployment_name="logic-apps-consumption-only"
deployment_args=(
  --subscription "$AZURE_SUBSCRIPTION_ID"
  --resource-group "$LOGIC_APP_RESOURCE_GROUP"
  --name "$deployment_name"
)

command_name=what-if
state=Disabled
if [[ "$mode" == what-if ]]; then
  case "${LOGIC_APP_PREVIEW_STATE:-Disabled}" in
    Disabled|Enabled) state="${LOGIC_APP_PREVIEW_STATE:-Disabled}" ;;
    *) printf 'LOGIC_APP_PREVIEW_STATE must be Disabled or Enabled.\n' >&2; exit 2 ;;
  esac
fi
if [[ "$mode" == deploy || "$mode" == activate ]]; then
  if [[ "${CONFIRM_LOGIC_APP_DEPLOYMENT:-}" != yes ]]; then
    printf 'Set CONFIRM_LOGIC_APP_DEPLOYMENT=yes to authorize the Consumption deployment.\n' >&2
    exit 2
  fi
  command_name=create
fi
if [[ "$mode" == activate ]]; then
  if [[ "${CONFIRM_LOGIC_APP_ACTIVATION:-}" != yes ]]; then
    printf 'Set CONFIRM_LOGIC_APP_ACTIVATION=yes to enable delegated manual tests. This does not certify runtime acceptance.\n' >&2
    exit 2
  fi
  state=Enabled
fi
parameters=(
  "@$temp_dir/parameters.json"
  "workflowState=$state"
  location="$LOGIC_APP_LOCATION"
  baseName="${LOGIC_APP_BASE_NAME:-projplan}"
)
if [[ -n "${LOGIC_APP_LOG_WORKSPACE_ID:-}" ]]; then
  parameters+=("logAnalyticsWorkspaceResourceId=$LOGIC_APP_LOG_WORKSPACE_ID")
fi
az deployment group "$command_name" "${deployment_args[@]}" \
  --mode Incremental --template-file "$temp_dir/template.json" --parameters "${parameters[@]}"

if [[ "$command_name" == create ]]; then
  id="$(az deployment group show "${deployment_args[@]}" \
    --query properties.outputs.logicAppResourceId.value --output tsv)"
  expected_prefix="/subscriptions/$AZURE_SUBSCRIPTION_ID/resourceGroups/$LOGIC_APP_RESOURCE_GROUP/providers/Microsoft.Logic/workflows/"
  if [[ "$id" != "$expected_prefix"* ]]; then
    printf 'Deployment returned an unexpected workflow resource ID.\n' >&2
    exit 1
  fi
  deployed_state="$(az resource show --ids "$id" --api-version 2019-05-01 \
    --query properties.state --output tsv)"
  if [[ "$deployed_state" != "$state" ]]; then
    printf 'Workflow state verification failed: expected %s, received %s.\n' "$state" "$deployed_state" >&2
    exit 1
  fi
  if [[ "$mode" == activate ]]; then
    printf 'Workflow enabled for manual tests. No Fabric inference was run; supply delegated user authentication using scripts/test_logic_app.py.\n'
  fi
fi
