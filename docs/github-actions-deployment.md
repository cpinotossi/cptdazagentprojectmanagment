---
title: GitHub Actions deployment with Entra federation
description: Separate infrastructure validation, approved identity bootstrap and manual Consumption deployment
---

## Pipeline boundaries

| Workflow | Trigger | Azure access | Scope |
|----------|---------|--------------|-------|
| [Infrastructure CI](../.github/workflows/infra-ci.yml) | Relevant pull requests and pushes to main; manual | None | Compile Bicep and run focused infrastructure tests |
| [Logic Apps deployment](../.github/workflows/deploy-logic-app.yml) | Manual, main only | OIDC through environment `azure-demo` | Preview or deploy only the Consumption module |
| [Foundry datasets](../.github/workflows/foundry-datasets.yml) | Relevant pull requests and main pushes validate; manual publication from main | OIDC through separate environment `foundry-demo` for publication only | Validate and publish committed JSONL datasets |

CI does not request an OIDC token or install the application dependencies.
Actions are pinned to verified commit SHAs. The deployment workflow never invokes
the full deployment script, Fabric setup, ontology publication or agent updates.
It preserves the disabled preparation state and verifies it after deployment.

The [Foundry dataset bootstrap](foundry-datasets.md#administrative-identity-bootstrap)
uses a separate UAMI with Foundry User only at the project scope. Its identity,
federation, main-only environment and variables have been provisioned; the first
OIDC upload run still requires publishing the new workflow.

Keep future full-infrastructure and Fabric/agent publication workflows separate.
Full infrastructure currently contains role assignments and needs a different
authorization design. Fabric publication also needs a proven supported identity
flow; the deployment identity does not solve ontology runtime authentication.

## Separate administrative bootstrap

The [bootstrap template](../infra/deployment-identity.bicep) creates only:

* User-assigned managed identity `id-projplan-github-deploy`.
* One GitHub environment federated credential.
* Contributor on the existing resource group `rg-projplan-demo`.

The approved Azure bootstrap was deployed and independently read back. No Logic
App or Fabric resource was deployed by this bootstrap. Repeat only as an
administrator with role-assignment permissions:

```bash
az deployment group what-if \
  --subscription 98525264-1eb4-493f-983d-16a330caa7f6 \
  --resource-group rg-projplan-demo \
  --name github-oidc-bootstrap \
  --template-file infra/deployment-identity.bicep --mode Incremental

az deployment group create \
  --subscription 98525264-1eb4-493f-983d-16a330caa7f6 \
  --resource-group rg-projplan-demo \
  --name github-oidc-bootstrap \
  --template-file infra/deployment-identity.bicep --mode Incremental
```

The deployment workflow does not run this bootstrap or grant itself roles.
Contributor cannot create role assignments. However, Contributor can modify
other resources, including federated credentials on identities in this resource
group. The workflow's narrow deployment target is not an Azure authorization
boundary. Consider a dedicated deployment resource group or narrower custom role
before extending access to additional environments.

The federation uses:

```text
issuer:   https://token.actions.githubusercontent.com
audience: api://AzureADTokenExchange
subject:  repo:cpinotossi@8876980/cptdazagentprojectmanagment@1442137267:environment:azure-demo
```

GitHub's immutable subject format includes both the owner ID and repository ID.
It is the default for repositories created after July 15, 2026 and also applies
after relevant renames or transfers. The first live login exposed a mismatch
with the old name-only template. Use the exact subject emitted by GitHub; do not
remove IDs or weaken the trust to fix an `AADSTS700213` error.

This trusts the environment, not one specific workflow. Protect the environment,
main branch and workflow changes; any eligible workflow referencing that
environment can request the token.

## GitHub configuration and remaining protection

Environment `azure-demo`, its main-only branch deployment policy and all six
variables below have been created in GitHub and read back. No reviewer approval
rule is configured. Main is currently unprotected, and the rulesets API rejects
access for this private repository with an upgrade requirement.

Before using this beyond the current manually triggered demo:

1. Configure required reviewers and prevent self-review where your repository
   visibility and GitHub plan support them. Disable bypass of environment
   protections where available.
2. Protect main against unreviewed workflow changes. Manual dispatch and an
   environment branch restriction are not independent approval.
3. Review, commit and publish workflow updates through your normal Git process.
   The initial workflow files are published on main and Infrastructure CI passed.

The configured IDs are not credentials; no client secret is created or required.

| Environment variable | Value |
|----------------------|-------|
| `AZURE_CLIENT_ID` | `be499f20-5eb8-47b5-8ff7-5c40341ecd93` |
| `AZURE_TENANT_ID` | `f71980b2-590a-4de9-90d5-6fbc867da951` |
| `AZURE_SUBSCRIPTION_ID` | `98525264-1eb4-493f-983d-16a330caa7f6` |
| `AZURE_RESOURCE_GROUP` | `rg-projplan-demo` |
| `AZURE_LOCATION` | `swedencentral` |
| `LOGIC_APP_LOG_WORKSPACE_ID` | `/subscriptions/98525264-1eb4-493f-983d-16a330caa7f6/resourceGroups/rg-projplan-demo/providers/Microsoft.OperationalInsights/workspaces/log-projplan-axowf3` |

To reproduce the variables in an already created and protected environment:

```bash
repo=cpinotossi/cptdazagentprojectmanagment
gh variable set AZURE_CLIENT_ID --repo "$repo" --env azure-demo \
  --body be499f20-5eb8-47b5-8ff7-5c40341ecd93
gh variable set AZURE_TENANT_ID --repo "$repo" --env azure-demo \
  --body f71980b2-590a-4de9-90d5-6fbc867da951
gh variable set AZURE_SUBSCRIPTION_ID --repo "$repo" --env azure-demo \
  --body 98525264-1eb4-493f-983d-16a330caa7f6
gh variable set AZURE_RESOURCE_GROUP --repo "$repo" --env azure-demo \
  --body rg-projplan-demo
gh variable set AZURE_LOCATION --repo "$repo" --env azure-demo \
  --body swedencentral
gh variable set LOGIC_APP_LOG_WORKSPACE_ID --repo "$repo" --env azure-demo \
  --body '/subscriptions/98525264-1eb4-493f-983d-16a330caa7f6/resourceGroups/rg-projplan-demo/providers/Microsoft.OperationalInsights/workspaces/log-projplan-axowf3'
```

## Preview and deployment

1. Dispatch the deployment workflow on main with `operation=what-if`.
2. Approve access to the environment if required. Inspect the preview in the run
   logs and confirm that only the Consumption workflow and diagnostics change.
3. Dispatch a second run with `operation=deploy`. Review its commit SHA and target
   before approving. The job repeats tests and what-if before applying the module.
4. Check the post-deployment disabled-state verification and run summary.

Environment approval occurs before the job, not between its what-if and apply
steps. Separate runs provide manual review, not a cryptographically bound saved
plan; if main changes, preview the new revision. Deployment runs are serialized.
Do not run the full main deployment concurrently.

Local tests, template compilation and live bootstrap readback have passed.
The workflow YAML has passed actionlint. Use a what-if run to verify GitHub-issued
OIDC login without deploying. Actual Logic Apps deployment is a separate manual
operation. This preparation intentionally does not activate the workflow or implement the
ontology integration described in the
[Consumption guide](logic-apps-consumption.md).

## References

* [Azure Login with OIDC, including managed identities](https://learn.microsoft.com/en-us/azure/developer/github/connect-from-azure-openid-connect)
* [Managed identity federated credential Bicep reference](https://learn.microsoft.com/en-us/azure/templates/microsoft.managedidentity/userassignedidentities/federatedidentitycredentials)
* [GitHub deployment environments and protection rules](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments)
* [GitHub immutable OIDC subject claims](https://docs.github.com/en/actions/reference/security/oidc#immutable-subject-claims)
