---
title: Full Azure infrastructure deployment pipeline
description: Deploy the main Bicep template with the existing OIDC identity and constrained Foundry role delegation
---

## Scope and deployment boundaries

The [Azure infrastructure workflow](../.github/workflows/deploy-infrastructure.yml)
deploys the complete [main template](../infra/main.bicep):

* Foundry account and project
* Chat and embedding model deployments
* Fabric capacity
* Log Analytics, Application Insights and the project tracing connection
* The disabled Consumption workflow and diagnostics
* The two human Foundry access assignments

It does not invoke [deploy.sh](../deploy.sh), import Fabric data, publish the
ontology, create Foundry agent versions or activate the Logic App. Those tasks
have separate release and authentication requirements.

The existing deployment identities and their federations remain administrative
bootstrap resources. The pipeline does not create its own credentials or grant
itself deployment access. An additional debugging model and the automatically
created Application Insights action group are not declared in main. Incremental
deployment preserves undeclared resources; it does not bring them under IaC or
remove them.

## Existing identity and restricted role management

The workflow reuses environment `azure-demo` and UAMI
`id-projplan-github-deploy`. No additional service principal, federated credential
or GitHub environment was created.

The identity retains Contributor on `rg-projplan-demo`. The approved
[RBAC extension](../infra/infrastructure-rbac.bicep) adds Role Based Access
Control Administrator only at the existing Foundry account. Its condition applies
to both role-assignment writes and deletes and permits only:

* Foundry User and Foundry Project Manager
* The existing human administrator configured for this demo
* Principals of type User

This is not unrestricted Owner or User Access Administrator access. It cannot
grant these roles to the pipeline itself. Changes to the authorized target or
allowed roles require an administrator to update the delegation first. The
existing RG Contributor permission remains broad and includes identity/resource
modification; the condition is not a complete isolation boundary.

The extension was deployed and read back. Repeat only as an administrator:

```bash
az deployment group what-if \
  --subscription 98525264-1eb4-493f-983d-16a330caa7f6 \
  --resource-group rg-projplan-demo \
  --name github-infrastructure-rbac \
  --template-file infra/infrastructure-rbac.bicep \
  --parameters accessPrincipalId="$FOUNDRY_ACCESS_PRINCIPAL_ID" --mode Incremental

az deployment group create \
  --subscription 98525264-1eb4-493f-983d-16a330caa7f6 \
  --resource-group rg-projplan-demo \
  --name github-infrastructure-rbac \
  --template-file infra/infrastructure-rbac.bicep \
  --parameters accessPrincipalId="$FOUNDRY_ACCESS_PRINCIPAL_ID" --mode Incremental
```

The template references the existing account, so this bootstrap is for the
existing demo environment, not an unattended new-subscription installation.

## Explicit environment parameters

[demo.bicepparam](../infra/demo.bicepparam) pins the region, base name, models,
versions and capacities. Fabric SKU is explicitly `F4`, matching the live
capacity; it does not inherit the older main-template default of F2.

The existing `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID` and
`AZURE_RESOURCE_GROUP` variables remain unchanged. Two administrator inputs have
been configured in `azure-demo`:

* Environment variable `FOUNDRY_ACCESS_PRINCIPAL_ID` identifies the approved
  human role-assignment target, not the OIDC identity.
* Environment secret `FABRIC_ADMIN_UPN` supplies the existing Fabric capacity
  administrator.

The UPN is not committed or included in the run summary. It is a secure Bicep
parameter. Deployment targets are fixed to the approved tenant, subscription and
resource group; a mismatch fails before any deployment.

## Check, preview and apply

The manual workflow defaults to `operation=check`, which needs no Azure login:

```bash
bash scripts/deploy_infrastructure.sh check
```

This compiles the template and environment parameter file with clearly marked
validation-only placeholders. Actual preview and apply require the real
administrator inputs; there is no cloud fallback to placeholder values.

1. Review and publish the workflow through the normal Git process.
2. Dispatch `operation=what-if` on main. Review the complete resource diff.
3. Dispatch `operation=deploy` separately only after approving that preview.
   The job repeats what-if before applying in Incremental mode.
4. Review outputs and post-deployment checks. The Logic App must remain Disabled
   and the Fabric SKU must match the deployed parameter.

For a locally authenticated operator with the same configured environment:

```bash
bash scripts/deploy_infrastructure.sh what-if
CONFIRM_INFRASTRUCTURE_DEPLOYMENT=yes bash scripts/deploy_infrastructure.sh deploy
```

Full infrastructure and the existing Consumption-only workflow share the
`azure-demo-infrastructure` concurrency group, preventing simultaneous runs of
those two workflows. Do not concurrently use the legacy local deployment script
or change model resources while publishing agent releases.

No reviewer rule is configured and main remains unprotected. Manual dispatch
and an environment branch restriction are not independent approval. A local
full-template preview succeeded, but it proposed changes, including creation of
the disabled Logic App. Some differences involve server-maintained fields and
secure/reference values; do not assume the entire diff is harmless. No
application-infrastructure apply was performed, and the new workflow has not
yet been live-tested with GitHub-issued OIDC.

## References

* [Delegate Azure role assignment management with conditions](https://learn.microsoft.com/en-us/azure/role-based-access-control/delegate-role-assignments-examples)
* [Bicep parameter files and environment variables](https://learn.microsoft.com/en-us/azure/azure-resource-manager/bicep/parameter-files)
