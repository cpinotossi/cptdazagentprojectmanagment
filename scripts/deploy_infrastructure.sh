#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

mode="${1:-check}"
case "$mode" in
  check|what-if|deploy) ;;
  *) printf 'Usage: bash scripts/deploy_infrastructure.sh [check|what-if|deploy]\n' >&2; exit 2 ;;
esac
if [[ $# -gt 1 ]]; then
  printf 'Only one mode argument is accepted.\n' >&2
  exit 2
fi
if [[ "$mode" == deploy && "${CONFIRM_INFRASTRUCTURE_DEPLOYMENT:-}" != yes ]]; then
  printf 'Set CONFIRM_INFRASTRUCTURE_DEPLOYMENT=yes to authorize the full deployment.\n' >&2
  exit 2
fi

if [[ "$mode" == check ]]; then
  export FOUNDRY_ACCESS_PRINCIPAL_ID=00000000-0000-0000-0000-000000000000
  export FABRIC_ADMIN_UPN=validation@example.invalid
else
  : "${AZURE_SUBSCRIPTION_ID:?Set the target subscription}"
  : "${AZURE_TENANT_ID:?Set the target tenant}"
  : "${AZURE_RESOURCE_GROUP:?Set the existing resource group}"
  : "${FOUNDRY_ACCESS_PRINCIPAL_ID:?Set the approved human Foundry access principal}"
  : "${FABRIC_ADMIN_UPN:?Set the human Fabric capacity administrator}"
  if [[ "$AZURE_SUBSCRIPTION_ID" != 98525264-1eb4-493f-983d-16a330caa7f6 ||
        "$AZURE_TENANT_ID" != f71980b2-590a-4de9-90d5-6fbc867da951 ||
        "$AZURE_RESOURCE_GROUP" != rg-projplan-demo ]]; then
    printf 'Subscription, tenant and resource group must match the approved demo target.\n' >&2
    exit 2
  fi
fi

temp_dir="$(mktemp -d)"
trap 'rm -f -- "$temp_dir/main.json" "$temp_dir/parameters.json"; rmdir -- "$temp_dir"' EXIT
az bicep build --file infra/main.bicep --outfile "$temp_dir/main.json"
az bicep build-params --file infra/demo.bicepparam --outfile "$temp_dir/parameters.json"
if [[ "$mode" == check ]]; then
  printf 'Full infrastructure and demo parameters compiled with validation-only placeholders. No Azure access.\n'
  exit 0
fi

tenant="$(az account show --subscription "$AZURE_SUBSCRIPTION_ID" --query tenantId --output tsv)"
if [[ "$tenant" != "$AZURE_TENANT_ID" ]]; then
  printf 'The Azure session tenant does not match the approved target.\n' >&2
  exit 2
fi
command_name=what-if
extra_args=(--no-pretty-print)
if [[ "$mode" == deploy ]]; then
  command_name=create
  extra_args=()
fi
az deployment group "$command_name" \
  --subscription "$AZURE_SUBSCRIPTION_ID" --resource-group "$AZURE_RESOURCE_GROUP" \
  --name azure-infrastructure --mode Incremental \
  --template-file "$temp_dir/main.json" --parameters "@$temp_dir/parameters.json" \
  "${extra_args[@]}" \
  --output json
