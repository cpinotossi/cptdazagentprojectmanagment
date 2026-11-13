#!/usr/bin/env bash
# Deploy only the Consumption module, without touching Fabric or Foundry.
set -euo pipefail
cd "$(dirname "$0")/.."

mode="${1:-check}"
case "$mode" in
  check|what-if|deploy) ;;
  *) printf 'Usage: bash scripts/deploy_logic_app.sh [check|what-if|deploy]\n' >&2; exit 2 ;;
esac
if [[ $# -gt 1 ]]; then
  printf 'Only one mode argument is accepted.\n' >&2
  exit 2
fi

temp_dir="$(mktemp -d)"
trap 'rm -f -- "$temp_dir/template.json"; rmdir -- "$temp_dir"' EXIT

template=infra/modules/logic-apps-consumption.bicep
az bicep build --file "$template" --outfile "$temp_dir/template.json"
if [[ "$mode" == check ]]; then
  printf 'Consumption Bicep compilation passed. No Azure deployment performed.\n'
  exit 0
fi

: "${AZURE_SUBSCRIPTION_ID:?Set the target subscription explicitly}"
: "${LOGIC_APP_RESOURCE_GROUP:?Set the existing target resource group}"
: "${LOGIC_APP_LOCATION:?Choose the Consumption workflow region}"
deployment_name="logic-apps-consumption-only"
deployment_args=(
  --subscription "$AZURE_SUBSCRIPTION_ID"
  --resource-group "$LOGIC_APP_RESOURCE_GROUP"
  --name "$deployment_name"
)

command_name=what-if
if [[ "$mode" == deploy ]]; then
  if [[ "${CONFIRM_LOGIC_APP_DEPLOYMENT:-}" != yes ]]; then
    printf 'Set CONFIRM_LOGIC_APP_DEPLOYMENT=yes to authorize the Consumption deployment.\n' >&2
    exit 2
  fi
  command_name=create
fi
parameters=(
  location="$LOGIC_APP_LOCATION"
  baseName="${LOGIC_APP_BASE_NAME:-projplan}"
)
if [[ -n "${LOGIC_APP_LOG_WORKSPACE_ID:-}" ]]; then
  parameters+=("logAnalyticsWorkspaceResourceId=$LOGIC_APP_LOG_WORKSPACE_ID")
fi
az deployment group "$command_name" "${deployment_args[@]}" \
  --mode Incremental --template-file "$template" --parameters "${parameters[@]}"
