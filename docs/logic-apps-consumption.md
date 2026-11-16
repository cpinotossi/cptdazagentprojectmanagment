---
title: Explicit Fabric IQ orchestration with delegated authentication
description: Consumption deployment and manual testing of ontology entity matching, query rewriting and ask_ontology
---

Executable walkthrough: [Bash notebook](./logic-apps-consumption.ipynb).

## What the workflow does

The [Consumption definition](../logicapps/ontology-orchestration/workflow.json)
calls the Fabric IQ ontology MCP endpoint directly, without invoking the Foundry
IQ agent:

1. Call `list_ontology_entities` without name filters to retrieve all entities,
   including properties, synonyms and source links.
2. Call `list_ontology_rules` without entity filters to retrieve every rule,
   including rules that would be missed by entity-only filtering.
3. Give this metadata and the complete original question to a Foundry model.
   Resolve entity references and produce an ontology-grounded query rewrite.
   Record relevance and a reason for every rule. A fact-dependent rule remains
   potentially applicable until evidence has been retrieved.
4. Verify entity names, exact rule coverage, unresolved semantics and request
   length. Send the unchanged original question plus the clarification to
   `ask_ontology` as its `request` argument.
5. Let Fabric IQ retrieve evidence through the ontology's bindings, including
   the semantic model where applicable. The workflow does not select SQL tables,
   reproduce semantic measures or substitute its own retrieval implementation.
6. Independently validate the returned answer against the original question,
   the rewrite, ontology entities and every rule. Return the original Fabric IQ
   answer only after successful model review and structural checks.

The [stage prompts](../logicapps/ontology-orchestration/prompts.json) contain
orchestration instructions, not business policies. Policies remain in the
ontology. An empty rule set is supported; nonempty sets require exact coverage.
There is no physical SQL connector, definition REST/TMDL download, tool-selection
agent, order placement or notification action in this workflow.


## Fabric IQ tools list

~~~bash
MCP_URL='https://api.fabric.microsoft.com/v1/mcp/dataPlane/workspaces/66d9c2b2-d35e-4555-9e74-926164ce584c/items/8750de7f-d46f-4dd4-84f4-a7e31f68ff24/ontologyEndpoint'

FABRIC_IQ_MCP=$(az rest --method post \
  --resource https://api.fabric.microsoft.com \
  --url "$MCP_URL" \
  --headers 'Content-Type=application/json' \
    'Accept=application/json, text/event-stream' \
  --body '{"jsonrpc":"2.0","id":"tools","method":"tools/list","params":{}}' -o json)
jq '.result.tools | map({
  name,
  description,
  parameters: (.inputSchema.properties | to_entries | map({
    name: .key,
    description: .value.description
  }))
})' <<< "$FABRIC_IQ_MCP"
~~~

## Fabric IQ list_ontology_entities

~~~bash
WORKSPACE_ID='66d9c2b2-d35e-4555-9e74-926164ce584c'
ONTOLOGY_ID='8750de7f-d46f-4dd4-84f4-a7e31f68ff24'
MCP_URL="https://api.fabric.microsoft.com/v1/mcp/dataPlane"
MCP_URL+="/workspaces/${WORKSPACE_ID}/items/${ONTOLOGY_ID}/ontologyEndpoint"

FABRIC_IQ_MCP_LIST_RESULT=$(az rest --method post \
  --resource https://api.fabric.microsoft.com \
  --url "$MCP_URL" \
  --headers 'Content-Type=application/json' \
    'Accept=application/json, text/event-stream' \
  --body '{
    "jsonrpc": "2.0",
    "id": "entities",
    "method": "tools/call",
    "params": {
      "name": "list_ontology_entities",
      "arguments": {}
    }
  }' -o json)
echo "$FABRIC_IQ_MCP_LIST_RESULT" | jq '.result.structuredContent.values | map({name, synonyms})'
~~~

~~~bash
FABRIC_IQ_MCP_LIST_RESULT=$(az rest --method post \
  --resource https://api.fabric.microsoft.com \
  --url "$MCP_URL" \
  --headers 'Content-Type=application/json' \
    'Accept=application/json, text/event-stream' \
  --body '{
    "jsonrpc": "2.0",
    "id": "entities",
    "method": "tools/call",
    "params": {
      "name": "list_ontology_entities",
      "arguments": {"entityName": "qwert"}
    }
  }' -o json)
echo "$FABRIC_IQ_MCP_LIST_RESULT" | jq '.result.structuredContent.values | map({name, synonyms})'
~~~


## Authentication boundaries

There are three separate authentication paths:

* The caller authenticates to the Logic App with an Entra token for
  `https://management.azure.com/`. The Request trigger checks tenant, issuer and
  an explicit caller object-ID allowlist. SAS authentication remains disabled.
* The caller supplies a delegated user token for
  `https://api.fabric.microsoft.com` in the request body's `fabricAccessToken`.
  Only the three fixed Fabric MCP HTTP actions use it. It is not a workflow
  parameter, deployment secret, model input, response field or poll header.
* The workflow uses the existing `id-projplan-github-foundry` Managed Identity
  only for the Foundry rewrite and validation model calls.

The Fabric user must have the supported access to the ontology and its bound
data sources. Granting the workflow identity more SQL or lakehouse permissions
does not replace this delegated access. The workflow does not implement OAuth
consent, on-behalf-of exchange or token renewal. Expired or insufficient tokens
fail explicitly; there is no SQL or Managed Identity fallback for Fabric IQ.

The trigger and credential-bearing MCP actions secure both inputs and outputs
in run history. Do not remove these settings to debug authentication. Tokens
still pass through the Logic Apps runtime; restrict workflow administration and
run-history access. Never put tokens in files, shell arguments, screenshots,
source control or model prompts.

The current endpoint was checked with delegated authentication:
`tools/list`, both unfiltered metadata tools and their structured response
shapes were verified. The supported response contract is JSON-RPC JSON with
`result.structuredContent.values` for metadata and text content for the answer.
Protocol errors, tool errors, malformed results and oversized payloads fail
closed. This implementation uses the verified stateless calls; it does not
parse SSE streams or implement a session-based MCP transport.

## Deployment

The [Bicep module](../infra/modules/logic-apps-consumption.bicep) deploys only the
Consumption workflow and optional diagnostics in the existing Log Analytics
workspace. It creates no hosting plan, storage account, SQL connection or new
identity. The Foundry project role assignment remains unchanged.

[Demo parameters](../infra/logic-apps-demo.bicepparam) load the nonsecret
[environment bindings](../logicapps/ontology-orchestration/deployment.json).
The [main template](../infra/main.bicep) embeds the same module. Both deployment
paths default to Disabled. The module-only helper does not publish Fabric
content, change the capacity, import data or release agents.

```bash
bash scripts/deploy_logic_app.sh check
PYTHONPATH=tests .venv/bin/python -m unittest test_logic_app_consumption -v

export AZURE_SUBSCRIPTION_ID='98525264-1eb4-493f-983d-16a330caa7f6'
export LOGIC_APP_RESOURCE_GROUP='rg-projplan-demo'
export LOGIC_APP_LOCATION='swedencentral'

bash scripts/deploy_logic_app.sh what-if
CONFIRM_LOGIC_APP_DEPLOYMENT=yes bash scripts/deploy_logic_app.sh deploy
```

For manual tests, explicitly enable the workflow:

```bash
CONFIRM_LOGIC_APP_DEPLOYMENT=yes CONFIRM_LOGIC_APP_ACTIVATION=yes \
  bash scripts/deploy_logic_app.sh activate
```

`activate` now means enable for delegated manual tests. It does not execute
Fabric inference, certify runtime acceptance or automatically disable after a
failed manual test. Disable the workflow after testing through the portal or
redeploy with `deploy`.

The [GitHub workflow](../.github/workflows/deploy-logic-app.yml) supports the same
operations using infrastructure OIDC. That identity deploys the workflow; it
does not impersonate a Fabric user or run a delegated smoke test.
Incremental deployment does not delete the old `sql-projplan-tbv74x` connection.
It is no longer referenced; removal and permission cleanup are separate
administrative operations.

## Manual tests

Sign in locally as an authorized user. This sign-in is deliberately separate
from deployment:

```bash
az login --tenant f71980b2-590a-4de9-90d5-6fbc867da951

RESOURCE_ID="$(az logic workflow show \
  --subscription 98525264-1eb4-493f-983d-16a330caa7f6 \
  --resource-group rg-projplan-demo --name logic-projplan-tbv74x \
  --query id --output tsv)"

echo $RESOURCE_ID

.venv/bin/python scripts/test_logic_app.py \
  --resource-id "$RESOURCE_ID" \
  --query 'Gib mir eine Liste aller qwert mit ihrer ID und ihrem Namen.'
```

The [manual client](../scripts/test_logic_app.py) obtains the ARM and Fabric
tokens separately from Azure CLI's existing user session. Tokens stay in memory
and are not printed. Interactive consent, if required, must be completed by the
user before running the test. The client will not initiate interactive login.
Only the original question and the Fabric access token enter the request body;
reference context, ground truth and evaluator criteria are not sent.

For a manual HTTP request, use an ARM Bearer token in the Authorization header
and this body shape, replacing the placeholder in memory rather than saving it:

```json
{
  "query": "Your complete original question",
  "fabricAccessToken": "<delegated Fabric access token>"
}
```

The legacy optional `context` property is accepted but ignored. The client polls
the asynchronous response using only the ARM token and rejects SAS links,
redirects, cross-host links and links to another workflow.

The success response contains only `response`, `validation` and `runId`.
The returned answer is the joined text content of the actual `ask_ontology`
result, not a locally generated replacement. Entity metadata, rule definitions,
the rewrite and the raw tool result remain internal; their checks still run.
Model validation is not independent verification of the underlying data.

Use `runId` to find the workflow run in Azure. Run history retains action status,
timing and failure location, subject to the configured retention. Secure inputs
and outputs remain hidden, so this is not a full content trace. Reducing the
response does not add Foundry/OpenTelemetry tracing or expose Fabric's internal
queries. The manual client validates the compact result; it no longer
cross-checks the internal metadata or raw answer, which are not returned.

### Call with HTTPie

Requires Bash, Azure CLI, HTTPie (`http`) and your existing user login.

```bash
SUBSCRIPTION='98525264-1eb4-493f-983d-16a330caa7f6'
RESOURCE_ID="$(az logic workflow show --subscription "$SUBSCRIPTION" \
  --resource-group rg-projplan-demo --name logic-projplan-tbv74x \
  --query id -o tsv)"

CALLBACK="$(az rest --method post \
  --url "https://management.azure.com${RESOURCE_ID}/triggers/\
When_a_request_is_received/listCallbackUrl?api-version=2019-05-01" \
  --query value -o tsv)"

ARM_TOKEN="$(az account get-access-token \
  --resource https://management.azure.com/ --query accessToken -o tsv)"

FABRIC_TOKEN="$(az account get-access-token \
  --resource https://api.fabric.microsoft.com --query accessToken -o tsv)"

QUESTION_B_006='Gib mir eine Liste aller qwert mit ihrer ID und ihrem Namen.'

http --ignore-stdin --print=hb --json POST "$CALLBACK" "Authorization:@"<(printf 'Bearer %s' "$ARM_TOKEN") query="$QUESTION_B_006" "fabricAccessToken=@"<(printf '%s' "$FABRIC_TOKEN")
```

~~~bash
http --ignore-stdin --print=hb --json POST "$CALLBACK" "Authorization:@"<(printf 'Bearer %s' "$ARM_TOKEN") query="$QUESTION_B_006" "fabricAccessToken=@"<(printf '%s' "$FABRIC_TOKEN")
HTTP/1.1 202 Accepted
Cache-Control: no-cache
Content-Length: 1067
Content-Type: application/json; charset=utf-8
Date: Mon, 16 Nov 2026 16:57:21 GMT
Expires: -1
Location: https://prod-07.swedencentral.logic.azure.com/workflows/d94b1945eaa34587ba79c76b84cdd22f/runs/08584067586442818169790444156CU27/operations/d5b5d497-a889-4e4f-8657-f169ee86eb5f?api-version=2019-05-01
Pragma: no-cache
Retry-After: 10
Strict-Transport-Security: max-age=31536000; includeSubDomains
x-ms-client-tracking-id: 08584067586442818169790444156CU27
x-ms-correlation-id: d5b5d497-a889-4e4f-8657-f169ee86eb5f
x-ms-execution-location: swedencentral
x-ms-ratelimit-burst-remaining-workflow-writes: 2811
x-ms-ratelimit-remaining-workflow-download-contentsize: 201326592
x-ms-ratelimit-remaining-workflow-upload-contentsize: 201324120
x-ms-ratelimit-time-remaining-directapirequests: 18749902
x-ms-request-id: swedencentral:d5b5d497-a889-4e4f-8657-f169ee86eb5f
x-ms-tracking-id: d5b5d497-a889-4e4f-8657-f169ee86eb5f
x-ms-trigger-history-name: 08584067586442818169790444156CU27
x-ms-workflow-id: d94b1945eaa34587ba79c76b84cdd22f
x-ms-workflow-name: logic-projplan-tbv74x
x-ms-workflow-run-id: 08584067586442818169790444156CU27
x-ms-workflow-system-id: /locations/swedencentral/scaleunits/prod-07/workflows/d94b1945eaa34587ba79c76b84cdd22f
x-ms-workflow-version: 08584067597425572070

{
    "id": "/workflows/d94b1945eaa34587ba79c76b84cdd22f/runs/08584067586442818169790444156CU27",
    "name": "08584067586442818169790444156CU27",
    "properties": {
        "correlation": {
            "clientTrackingId": "08584067586442818169790444156CU27"
        },
        "outputs": {},
        "response": {
            "correlation": {},
            "startTime": "2026-11-16T16:57:21.1866849Z",
            "status": "Waiting"
        },
        "startTime": "2026-11-16T16:57:21.1993526Z",
        "status": "Running",
        "trigger": {
            "correlation": {
                "clientTrackingId": "08584067586442818169790444156CU27"
            },
            "endTime": "2026-11-16T16:57:21.1866849Z",
            "inputsLink": {
                "contentSize": 267,
                "secureData": {
                    "properties": [
                        "inputs"
                    ]
                }
            },
            "name": "When_a_request_is_received",
            "originHistoryName": "08584067586442818169790444156CU27",
            "outputsLink": {
                "contentSize": 3657,
                "secureData": {
                    "properties": [
                        "outputs"
                    ]
                }
            },
            "startTime": "2026-11-16T16:57:21.1866849Z",
            "status": "Succeeded"
        },
        "waitEndTime": "2026-11-16T16:57:21.1993526Z",
        "workflow": {
            "id": "/workflows/d94b1945eaa34587ba79c76b84cdd22f/versions/08584067597425572070",
            "name": "08584067597425572070",
            "type": "Microsoft.Logic/workflows/versions"
        }
    },
    "type": "Microsoft.Logic/workflows/runs"
}
~~~


For HTTP 202, copy the `Location` response header into `POLL_URL`.
Run the GET again after a few seconds until HTTP 200 returns the result:

```bash
POLL_URL='https://prod-07.swedencentral.logic.azure.com/workflows/d94b1945eaa34587ba79c76b84cdd22f/runs/08584067586442818169790444156CU27/operations/d5b5d497-a889-4e4f-8657-f169ee86eb5f?api-version=2019-05-01'
http --ignore-stdin --print=hb GET "$POLL_URL"  "Authorization:@"<(printf 'Bearer %s' "$ARM_TOKEN")
```

## Errors and diagnostic stages

The run's `stage` and action names identify the failure location:

* `fabric-iq-metadata`: `List_ontology_entities`, `List_ontology_rules`,
  metadata validation or parsing
* `entity-matching-and-query-rewrite`: `Rewrite_query` and `Check_rewrite`
* `ask-ontology`: `Ask_ontology` and `Check_ontology_result`
* `answer-validation`: `Validate_answer` and `Check_validation`

HTTP transport failures, including Fabric 401/403, return HTTP 502 with the
stage and run ID. JSON-RPC/tool failures and invalid metadata, rewrites or
answers return explicit HTTP 422 errors. Failed runs never return a
success-shaped assessment.

The combined original question, instructions and clarification must fit
`ask_ontology`'s 10000-character request limit. Overlong input is rejected,
not truncated. Each metadata response and the ontology answer are bounded
to 120000 characters. Unsupported response formats fail rather than falling
back to direct SQL.

## Historical SQL comparison

The previous REST/SQL implementation passed a smoke test but only 1/6 diagnostic
cases. IQ v8 passed 6/6 using the same evaluator v7 and reference dataset.
Four workflow cases were rejected during source/rule screening and one failed
SQL login. That comparison changed the retrieval implementation as well as
orchestration, so it did not isolate the effect of explicit ordering.

Captures and judgments remain in the ignored
`evaluations/manual_runs/explicit-comparison-20261116/` directory.
Those scores do not evaluate this new MCP-based workflow. Its answer-quality
tests are intentionally left to the user's delegated manual execution.

## Microsoft references

* [Ontology MCP tools and authentication](https://learn.microsoft.com/en-us/fabric/iq/ontology/how-to-use-ontology-mcp-server)
* [Managed Identity for Foundry HTTP calls](https://learn.microsoft.com/en-us/azure/logic-apps/authenticate-with-managed-identity)
* [Workflow security and secure run history](https://learn.microsoft.com/en-us/azure/logic-apps/set-up-security-permissions)
* [Consumption workflow resource](https://learn.microsoft.com/en-us/azure/templates/microsoft.logic/2019-05-01/workflows)
