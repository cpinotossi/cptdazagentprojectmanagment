---
title: Consumption orchestration and infrastructure coverage
description: Integrated Bicep deployment, safe preparation status and remaining provisioning boundaries
---

## Consumption deployment

[main.bicep](../infra/main.bicep) includes the
[Consumption module](../infra/modules/logic-apps-consumption.bicep) by default.
Set `deployLogicApp=false` to omit the module from a deployment. In incremental
mode, omitting it does not delete an already deployed workflow.

Consumption deploys both the `Microsoft.Logic/workflows` resource and its
[workflow definition](../logicapps/ontology-orchestration/workflow.json) through
Bicep. No dedicated hosting plan, app service, storage account, host configuration
or ZIP publication is needed. The earlier, undeployed Standard preparation was
removed. This change does not migrate or delete any cloud resources.

The workflow has a system-assigned identity and is always deployed disabled.
The main deployment sends `WorkflowRuntime` diagnostics to its existing Log
Analytics workspace. No additional workspace or Application Insights is created.
No callback URL or token is emitted as a deployment output.

> [!IMPORTANT]
> The definition remains a nonfunctional integration scaffold, not an assessment
> workflow. If manually enabled, it returns HTTP 503 with
> `OntologyIntegrationNotConfigured` and marks the run as failed. Rule retrieval,
> semantic resolution, evidence retrieval and assessment are not implemented.
> Redeployment restores this definition and the disabled state.

## Validate and deploy without touching Fabric

```bash
# Local compilation only; no Azure resource deployment.
bash scripts/deploy_logic_app.sh check
.venv/bin/python -m unittest discover -s tests -p test_logic_app_consumption.py

# Select the existing project resource group explicitly.
export AZURE_SUBSCRIPTION_ID='<target-subscription-id>'
export LOGIC_APP_RESOURCE_GROUP='<existing-project-resource-group>'
export LOGIC_APP_LOCATION='swedencentral'

# Optional for module-only deployments; main.bicep supplies this automatically.
export LOGIC_APP_LOG_WORKSPACE_ID='<existing-workspace-resource-id>'

bash scripts/deploy_logic_app.sh what-if
CONFIRM_LOGIC_APP_DEPLOYMENT=yes bash scripts/deploy_logic_app.sh deploy
```

The helper deploys the same module with the same deterministic resource name as
the main deployment, using incremental mode. Keep `LOGIC_APP_BASE_NAME` (default
`projplan`), resource group and region aligned with the main deployment. If the
workflow already exists with diagnostics, supply the same workspace ID; omitting
the conditional diagnostic resource does not delete it in incremental mode.
Do not run main and module-only deployments concurrently.

The existing [deploy.sh](../deploy.sh) also includes the module through main.bicep.
However, that script subsequently runs Fabric setup and publishes an agent
version. Do not use it just to prepare Logic Apps while editing ontology or data.
The module-only helper does not import data, publish definitions, resize the Fabric
capacity or change Foundry-agent versions.

For isolated CI and manual module-only deployment through Entra workload identity
federation, follow the [GitHub Actions setup](github-actions-deployment.md).
The separate bootstrap identity has resource-group Contributor; it does not grant
the runtime workflow access to Fabric.

Consumption has execution-based charges rather than a dedicated WS1 hosting
charge. Connectors, diagnostics and model usage can incur additional charges.
Compilation and local tests are not proof of working cloud authentication.

## Designer

Consumption workflows can be visually edited in the Azure portal. The VS Code
Consumption extension provides a read-only designer and JSON editing, not local
visual editing. Standard's local visual designer cannot be assumed to support
Consumption. Consumption agent loops are a separate preview capability and are
not required for ordinary ordered workflow actions.

After any portal edits, transfer the reviewed definition into the repository.
Otherwise the next Bicep deployment overwrites those edits.

## Authentication and functional integration

The managed Foundry Agent Service connector documents an `Invoke Prompt Agent`
action with an agent version and Entra user-login authentication. Its connector
class named `Standard` is not a requirement for Standard hosting.

Before creating connection resources, prove Consumption -> pinned Foundry agent
-> Fabric IQ with the intended delegated user. Authentication to Foundry does
not establish automatic user delegation to Fabric. The ontology MCP documentation
currently excludes service principal access due to a known issue. A workflow
managed identity alone is therefore not a working ontology-access solution.

OAuth connection resource definitions and access policies can be deployed through
ARM/Bicep where supported, but user sign-in, consent and authorization still need
an explicit setup step. Do not store access tokens in workflow JSON or outputs.
Caller authentication and authorization on the Request trigger must also be
designed before activation; the disabled state is not a finished access policy.

Once authentication works, enforce the following ordinary workflow stages:

1. Retrieve the current complete rule catalog and semantic metadata.
2. Resolve names and declared synonyms using the ontology, not an exact-name
   entity filter.
3. Record applicable, not-applicable or unresolved status with a reason for every
   rule, including unlinked rules and property/relationship-linked rules.
4. Retrieve the evidence required by applicable rules.
5. Apply retrieved rules and validate coverage and evidence before answering.

An unresolved rule or failed retrieval must not yield a successful assessment.
Business definitions remain only in the ontology. Rule coverage checks cannot
guarantee correct LLM interpretation; evaluate both traces and business outcomes.
Direct agent calls bypass this external orchestrator, so the client must call
the workflow entry point. No notification delivery is introduced here.

## What Bicep covers

| Component | Current owner | Coverage |
|-----------|---------------|----------|
| Resource group | `deploy.sh` | Created by Azure CLI before the resource-group-scoped Bicep deployment |
| Foundry account and project, their identities | `main.bicep` | ARM resources, local authentication disabled |
| Chat and embedding deployments | `main.bicep` | Model versions, SKU and capacity |
| Fabric capacity and capacity administrator | `main.bicep` | Capacity resource and configured administrator |
| Log Analytics and Application Insights | `main.bicep` | Resources and Foundry tracing connection |
| Deployer Foundry roles | `main.bicep` | Foundry User and Project Manager at account scope |
| Consumption workflow, identity and runtime diagnostics | Consumption module | Disabled scaffold and definition; no operational integration |
| GitHub deployment identity, federation and RG Contributor | Separate `deployment-identity.bicep` bootstrap | Not invoked by main or the regular deployment workflow |
| Fabric workspace, lakehouse and capacity association | `setup.py` | Fabric REST, not the current Bicep template |
| CSV/PDF ingestion, Content Understanding configuration | Setup/import scripts | Data-plane analyzer/default configuration, extraction, upload and table loading |
| Semantic model, ontology and bindings | Setup/import scripts | TMDL publication, refresh and verification; no Bicep-managed business rules |
| Fabric-IQ project connection | `setup_agent.py` | ARM REST connection, currently not declared in Bicep |
| Foundry prompt agent and versions | Setup/update scripts | Projects SDK, not Bicep |
| Baseline agent, datasets, evaluators and evaluation runs | Evaluation/baseline scripts | Separate data-plane execution and provenance |

This is repository deployment coverage, not an audit of every live Azure resource.
Not every item outside Bicep is a defect. Fabric content and data operations should
remain separate from infrastructure provisioning.

## Gaps to close in priority order

1. Establish delegated Fabric authentication, connector permissions, caller
   authorization and a pinned agent invocation. This blocks an operational workflow.
2. Implement and evaluate the generic orchestration and its failure paths. This
   preparation does not make the explicit path available in notebooks yet.
3. Separate infrastructure-only deployment from bootstrap in the main entry point.
   The current full script always invokes Fabric setup after Bicep. The module-only
   helper provides a safe narrow path for this change.
4. Move the ARM-managed Fabric-IQ connection into Bicep after workspace/ontology
   identifiers are explicit deployment inputs. Avoid two independent deployment
   owners and do not hardcode a snapshot of the changing ontology.
5. Add environment parameter files and deployment identity/target checks. The main
   script currently uses the active CLI subscription and defaults Fabric to F2;
   a rerun can resize a capacity unintentionally.
6. Complete GitHub environment protections and variables for the prepared
   module-only pipeline, then verify OIDC and its first deployment. Functional
   smoke tests remain blocked on the integration. Keep data-plane provisioning
   explicitly opt-in and track resulting Fabric IDs and agent versions.
7. Review runtime least privilege, diagnostic retention, alerts/budgets and network
   requirements. Do not add broad roles or private networking without requirements.

Fabric tenant settings, consent and caller permissions remain administrative
prerequisites. Moving them into Bicep is not automatically supported or desirable.

## Microsoft references

* [Consumption workflow ARM/Bicep resource](https://learn.microsoft.com/en-us/azure/templates/microsoft.logic/2019-05-01/workflows)
* [Foundry Agent Service connector](https://learn.microsoft.com/en-us/connectors/azureagentservice/)
* [VS Code editing and designer limitations](https://learn.microsoft.com/en-us/azure/logic-apps/manage-logic-apps-visual-studio-code#edit-a-workflow)
* [Consumption agent-loop prerequisites](https://learn.microsoft.com/en-us/azure/logic-apps/create-autonomous-agent-workflows#prerequisites)
* [Ontology MCP authentication limitation](https://learn.microsoft.com/en-us/fabric/iq/ontology/how-to-use-ontology-mcp-server)
