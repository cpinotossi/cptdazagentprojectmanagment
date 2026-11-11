---
title: Construction Project Planning Agent (Demo)
description: Demo of a Microsoft Foundry agent that uses a Fabric ontology (Fabric IQ) to check project data, documents and emails of construction projects
---

## Idea

All data lives as tables in a Fabric lakehouse. A semantic model on top of them holds the relationships and a few DAX measures for exact numbers (duration, planned cost, budget variance). A Fabric ontology is bound to that semantic model and gives it business meaning and rules. The Foundry agent reaches all of it through one tool, Fabric IQ.

```text
Project ──Managed By──▶ ProjectManager
ProjectDocument ──Of Project──▶ Project   (specification, offer, contract; Content = PDF text)
Email ──About Project──▶ Project          (rated by the ontology rules)
```

The ontology also carries the business knowledge, so the agent prompt stays short:

* Descriptions and synonyms for entities, properties and relationships
* Business rules: email criticality, escalation within 1 working day, change requests outside the specification

## Contents

| Path | Purpose |
|------|---------|
| `infra/main.bicep` | Foundry account, project, chat and embedding model, Fabric capacity F2, Application Insights for tracing, role assignments |
| `ontology/` | The ontology as TMDL files: entities, descriptions, synonyms, relationships, rules, and the tables bound to the semantic model |
| `semantic_model/` | The semantic model as TMDL files: the six tables over the lakehouse, their relationships and the DAX measures for duration, planned cost, budget and variance |
| `extraction/` | Azure Content Understanding analyzer for the PDFs and the known values of the sample documents |
| `scripts/setup.py` | Fabric workspace, lakehouse, tables, semantic model and ontology; calls `setup_agent.py` with the resulting IDs |
| `scripts/setup_agent.py` | Fabric IQ connection and Foundry agent version, called by `setup.py`; no inference request |
| `scripts/evaluate_agent.py` | Registers the test dataset and runs Foundry Groundedness against a pinned agent version |
| `scripts/content_understanding.py` | REST client for Azure Content Understanding, used by `setup.py` to read the terms from the PDFs |
| `scripts/update_ontology.py` | Uploads only the `ontology/` folder, used after a change and by the notebook to restore a rule |
| `scripts/agent_instructions.txt` | Short agent prompt: role, tool usage, no invented data |
| `data/` | Four small PDFs and CSV files as test data, amounts in EUR |
| `notebooks/use_case.ipynb` | Bash notebook that plays the use case: an email arrives, is it critical? |
| `notebooks/request_walkthrough.ipynb` | Bash notebook that follows one email through every layer, with a growing diagram, the configuration, the code and live evidence per chapter |
| `notebooks/project_synonym_test.ipynb` | Bash notebook with a temporary negative control, then restores the permanent Project synonym `qwert` |
| `docs/how-it-works.md` | Explanation of the solution for readers who know Foundry but not Fabric or ontologies |
| `docs/configuration-reference.md` | Every setting of the current setup, file by file, with Microsoft references, portal deep links, strengths and weaknesses |
| `docs/microsoft-guidance.md` | Where the demo follows the Microsoft documentation, where it goes beyond it, and what stays open |
| `deploy.sh` | Runs both steps one after the other |

Bicep covers the Azure resources only. Fabric content and the agent live on the data plane.
[setup.py](scripts/setup.py) creates the Fabric content, then calls
[setup_agent.py](scripts/setup_agent.py) to create the connection and agent through REST and the SDK.

## Run

Prerequisites: Azure CLI (`az login`), Python 3, permission to create resources and role assignments. In the tenant, Fabric must be enabled, the resource provider `Microsoft.Fabric` registered and the Fabric tenant setting "Users can create Ontology (preview) items" switched on.

```bash
./deploy.sh
```

The script ends with a test question to the agent. The F2 capacity costs money while it runs. Pause it in the Azure portal or remove everything with `az group delete -n rg-projplan-demo`. F2 is enough for the agent. If you also work in the portal at the same time, it can report that the capacity limit is exceeded. Deploy with `FABRIC_SKU=F4 ./deploy.sh` then, because a deployment without the variable sets the size back to F2.

## Groundedness evaluation

[groundedness_tests.json](evaluations/groundedness_tests.json) contains ten German
test cases: five document cases and five email cases based on the current PDFs,
CSV data and ontology rules. Each case includes a question (`query`),
verbatim document excerpts (`context`), an expected answer (`ground_truth`), required
facts and source references. The document cases cover facts, a document comparison,
a calculation and an unsupported question that must not receive an invented answer.
The email cases cover all five messages in `data/emails.csv`: routine meeting,
delayed permit, green roof with photovoltaics, holiday closure and cost warning.
They ask for the associated `qwert`, its project manager, criticality and escalation.
Expected ratings are not critical, critical, critical, not critical and critical.
The holiday case resolves the year from the email date; the cost case checks the
EUR 750,000 / 15 percent forecast overrun and required written approval.

The JSON file is the reference dataset, including PDF hashes, not evaluation results.
[groundedness_tests.jsonl](evaluations/groundedness_tests.jsonl) contains the same
ten cases as one JSON object per line for upload as a Foundry dataset.

In the Foundry evaluation wizard, select **Agent**, **Individual turns** and
**Existing dataset**. Select the uploaded dataset and use `{{item.query}}` as the
agent user prompt. Select **Groundedness**, configure the judge model and map the
question, generated response and supporting `context` fields as required by the
wizard. Foundry generates `response` by calling the agent; the input file does not
contain invented or placeholder responses. Do not send `ground_truth` or `context`
as part of the agent prompt: they are evaluation references.

For evaluation of existing outputs instead, add the actual `response` to each row
before upload. Evaluate correctness and completeness against `ground_truth`
separately. For end-to-end retrieval evaluation, also capture the actual tool
results; the curated context does not prove what the agent retrieved.

See [portal evaluations](https://learn.microsoft.com/azure/foundry/how-to/evaluate-generative-ai-app)
and [evaluation dataset schema](https://learn.microsoft.com/azure/foundry/observability/how-to/evaluation-dataset-schema).

### Run the evaluation from the command line

[evaluate_agent.py](scripts/evaluate_agent.py) registers the JSONL file as a Foundry
dataset, creates a Groundedness evaluation and invokes the existing agent through
the native `azure_ai_agent` evaluation target. It does not create or modify agents,
their instructions, connections or the ontology.

```bash
export FOUNDRY_PROJECT_ENDPOINT="$(az deployment group show \
  -g rg-projplan-demo -n main \
  --query properties.outputs.foundryProjectEndpoint.value -o tsv)"
.venv/bin/python scripts/evaluate_agent.py
```

By default the script resolves and pins the latest published
`project-planning-agent` version. Use `--agent-version 7` to select an explicit
version. The judge uses the existing `gpt-4.1` deployment; `--judge-model` selects
another existing deployment. Running the agent and judge incurs inference charges.
The built-in Groundedness evaluator version is also resolved and pinned. Scores
range from 1 to 5; the standard threshold is 3, with scores at or above 3 passing.

Only `query` is sent to the agent. The evaluator maps the generated
`sample.output_text` to `response` and the dataset's reference `context` to grounding
evidence. `ground_truth` is not used as the generated response or sent to the agent.
The script uses the public OpenAI client's `post` method for Azure-specific
evaluator and agent-target request extensions.

Dataset versions use a content-hash prefix, so unchanged input data is reused.
Each new invocation creates an evaluation and run. The script saves the IDs,
configuration, run status, report URL and complete per-case output under
`evaluations/runs/<RUN_ID>.json`. A timeout preserves the run so it can be resumed
without submitting the cases again:

```bash
.venv/bin/python scripts/evaluate_agent.py \
  --resume evaluations/runs/<RUN_ID>.json
```

The default wait limit is 1,800 seconds (`--timeout`). Failed runs, errored cases and
missing results cause a nonzero exit; low but valid Groundedness scores remain
visible evaluation results, not execution errors. Groundedness against the curated
reference context is not a completeness, correctness or retrieval-quality score.

SDK references: [agent-target evaluation](https://learn.microsoft.com/azure/foundry/observability/how-to/cloud-evaluation-targets),
[dataset registration](https://learn.microsoft.com/azure/foundry/observability/how-to/cloud-evaluation-datasets)
and [Groundedness evaluator](https://learn.microsoft.com/azure/foundry/concepts/evaluation-evaluators/rag-evaluators).

## Notebook

`notebooks/use_case.ipynb` asks the deployed agent about two incoming emails through the Foundry Responses API. Section 5 then follows the last call through Foundry and Fabric: the agent configuration, the trace in Application Insights, the ontology configuration and the SQL that Fabric ran. Running the whole notebook takes about eight minutes, because the trace and the Fabric query log appear a few minutes after the call.

`notebooks/request_walkthrough.ipynb` follows a single email from the Foundry agent through the connection, the ontology, the semantic model and the SQL endpoint down to the lakehouse tables and the PDF extraction, and back. Each chapter starts with a diagram that grows with the request, prints the configuration files and code of its component, and shows live evidence. The DAX and SQL chapters read the Fabric workspace monitoring log, so they need a monitoring Eventhouse in the workspace (workspace settings, Monitoring). Running it takes 10 to 20 minutes.

It needs `jq`, `curl`, `column`, `az login`, a running Fabric capacity and the Bash kernel in the project environment. Section 5.5 downloads `sqlcmd` (Linux x64) to `.tools/` on first use.

```bash
.venv/bin/pip install bash_kernel && .venv/bin/python -m bash_kernel.install --sys-prefix
```

In VS Code select the kernel **Bash** (`.venv/share/jupyter/kernels/bash`). Every code cell must show **Shell Script**.

### Project synonym experiment

[project_synonym_test.ipynb](notebooks/project_synonym_test.ipynb) tests whether the
agent resolves `qwert` through the ontology. This synonym is a permanent part of
[Project.tmdl](ontology/entities/Project.tmdl), not an agent prompt mapping.
The notebook temporarily uploads a derived negative-control definition without
`qwert`. It tests the normal question and the extended diagnostic question without
the synonym, uploads the permanent repository definition, and repeats both requests.
The normal question contains no tool names or retrieval instructions.
Generic routing rules live in [agent_instructions.txt](scripts/agent_instructions.txt):
business-data questions use `ask_ontology`; metadata tools are for schema exploration.
An empty exact-name metadata lookup is not evidence that business records are absent.
The ontology remains the source of synonym mappings; no mapping is hard-coded in the prompt.

[update_agent_instructions.py](scripts/update_agent_instructions.py) updates the
existing agent only when its stored instructions differ from the repository.
It preserves the model, tools and other definition settings and prints the published
version. The notebook pins that version in all four independent requests.
There is no additional wait. Tool selection remains automatic, not API-forced.
Requests use `az rest` and the existing `project-planning-agent`.
All four use the same `--query` projection to show response status/errors, available
tool names, executed tool calls with arguments/results, and the final answer.
Tool schemas, agent instructions, IDs and token statistics are omitted.
This filter affects only the displayed response, not the agent request.
Every JSON response is printed directly through `jq .`, without a Python output
wrapper or added request/diagnostics objects.
`calls[].arguments` shows the question forwarded to a tool. It can differ from the
original user input; the complete sent requests remain visible in the preparation cells.
HTTP failures propagate through `&&`.
[check_agent_response.jq](scripts/check_agent_response.jq) validates the already
displayed response without changing it. Incomplete responses and API/tool failures,
including nested service errors in `additionalProperties._meta`, return a nonzero exit code.

The observed `raiPostToolAnnotations` error says
`Error during output moderation for tool: ask_ontology for general harms.`
It is already present in the service's tool result, not introduced by local formatting.
Read-only control requests reproduced it for ordinary German and English project
questions without `qwert`, with completed tool calls and returned project records.
A schema lookup through `list_ontology_entities` did not contain that annotation.
The model deployment uses `Microsoft.DefaultV2`; no custom safety policy is configured
in the repository. These observations isolate the issue to the tool-output moderation
path, but do not establish which backend component failed or why.
The annotation remains visible in `calls[].result` and is not treated as a clean
moderation result or as evidence that harmful content was detected.
Do not disable safety filters to hide it. For service-side investigation, retain the
response ID and the ontology tool's conversation ID when opening a Microsoft support case.
No separate agent is created or deleted; changed instructions create a new version.

The canonical entity is [ontology/entities/Project.tmdl](ontology/entities/Project.tmdl).
A generated copy is stored in
[notebooks/fixtures/project_synonym/Project.tmdl](notebooks/fixtures/project_synonym/Project.tmdl).
The build script reads the canonical entity, not the generated copy.
Only the negative control removes `synonym qwert` from the Project upload part;
every other definition part is unchanged. The builder fails if the permanent
synonym is absent instead of silently generating identical test conditions.
The actual Fabric ontology changes when that payload is uploaded, not when
the fixture is edited.

Run the notebook cells in order to test directly against the existing demo.
Keep other ontology editors and deployment jobs stopped during the experiment.
There is no backup or local run folder.
[build_synonym_definitions.py](scripts/build_synonym_definitions.py) builds the
complete upload payloads from `ontology/` with the resolved environment values.
The notebook invokes it in one line and prints all resolved names, IDs, and the
ontology URL in the configuration cell. The generated files are
[fabric-ontology-repository.json](notebooks/fixtures/project_synonym/fabric-ontology-repository.json)
and [fabric-ontology-without-qwert.json](notebooks/fixtures/project_synonym/fabric-ontology-without-qwert.json).
[fabric-ontology-qwert.json](notebooks/fixtures/project_synonym/fabric-ontology-qwert.json)
is an identical copy of the permanent repository payload, retained for the positive upload.
Uploads read these files directly. Regenerate them after changing the ontology,
or the workspace/model bindings.
Upload, requests and reset remain separate cells in sections 6, 7 and 8.
Always run the reset cell in section 8, even if the request fails or you stop
the experiment early. There is no longer an automatic EXIT-handler reset.
It uploads the repository definition including permanent `qwert`; Fabric-only
changes are overwritten rather than preserved. The final metadata check fails
if `qwert` is missing. The final response check requires no project IDs in either
negative answer and exactly P-100/P-200 in both positive answers and tool results.
All four calls must start with `ask_ontology` and forward the term `qwert`.
Live status cells call `getDefinition` before the test upload, before reset,
and after reset. They show the deployed Project description, synonyms and
entity-level annotation lines, not the local fixture. No response is saved as
a backup. Synonyms are selected wherever they occur in the returned TMDL,
because Fabric can move them to the end of the entity during serialization.
No copy of the `ontology/` folder is created. Both JSON files contain the full
definition; the test payload replaces only the Project entity with the fixture.
The notebook uses a Bash kernel, `jq`, Azure CLI and REST. Its build one-liner
calls the Python script with the project environment's interpreter.
The comparison cell uses `jq` to decode the Project TMDL from both payloads
and `sed` to display the first 12 lines as plain text, including the synonyms.
TMDL remains the definition format;
JSON and Base64 are its transport envelope, not a conversion to JSON entities.
The [Update Ontology Definition API](https://learn.microsoft.com/rest/api/fabric/ontology/items/update-ontology-definition#payloadtype)
documents only `InlineBase64` for definition part payloads.
This API replaces the complete definition; it is not a partial entity update.
The [portal supports editing metadata on individual entities](https://learn.microsoft.com/fabric/iq/ontology/how-to-add-metadata).
However, the [documented public Ontology operations](https://learn.microsoft.com/rest/api/fabric/ontology/items)
do not expose an entity metadata PATCH endpoint.
The [public OpenAPI specification](https://github.com/microsoft/fabric-rest-api-specs/blob/main/ontology/swagger.json)
confirms this operation set. Its existing `PATCH /ontologies/{ontologyId}` changes
only the ontology item's display name or description, not a Project entity's
description, synonyms or annotations.
The portal live test on 2026-11-11 did confirm a targeted internal update:
`POST .../DigitalOperationsService/direct/v4/workspaces/{workspaceId}/ontologies/{ontologyId}/definition/scripts`
on the capacity-specific `pbidedicated.windows.net` host.
The captured reset request was a 107-byte JSON `alter` command targeting
`Project` and replacing only its synonyms; it returned HTTP 200.
The temporary synonym was removed and the restored state verified.
This internal endpoint is documented here only: the notebook and scripts do
not use it. They continue to use the public `updateDefinition` API.
The payload contains ontology metadata, not the underlying project data.
[upload_ontology_definition.py](scripts/upload_ontology_definition.py) reads the
selected JSON file, uploads it and waits for asynchronous operations. It reuses
the project's Azure CLI authentication and reports HTTP errors, failed operations
and polling timeouts. Upload and reset cells each contain one Python script call.
After a kernel termination, follow the manual reset instructions.
Answers remain in the notebook outputs.
The notebook does not edit the canonical local entity. This experiment tests synonyms,
not the effect of `///` descriptions or the model's internal reasoning.

The `IPKernelApp` warning about unencrypted TCP comes from Jupyter's local kernel
transport, not the Fabric API. The notebook runner uses the default TCP transport
on `127.0.0.1` without CurveZMQ encryption. Fabric and Foundry requests still use
HTTPS. IPC or kernel-manager-provisioned CurveZMQ can protect the kernel channel;
do not expose unencrypted kernel ports to a network.

## Ontology files

`ontology/` holds the Fabric ontology definition format (TMDL), the same files that the Fabric REST API and Git integration use. The TMDL files contain the concrete workspace, semantic model and lakehouse SQL endpoint values of this demo so you can follow the bindings without resolving placeholders. Before deploying to a different environment, update [ontology/expressions.tmdl](ontology/expressions.tmdl), the `ONT_*` source annotations in [ontology/tables/](ontology/tables), and [semantic_model/definition/expressions.tmdl](semantic_model/definition/expressions.tmdl). The shared expression name `SemanticModelSource` can stay the same across environments. `setup.py` uploads these values unchanged. Each partition carries six `ONT_*` annotations identifying its semantic model source. Adding them fixed the portal's Project Instances view, which previously could not identify the semantic model. The tables use `mode: directQuery` with the semantic model as source. The measures of `projects` and `tasks` are pointers (`EXTERNALMEASURE`) to the DAX measures in `semantic_model/`. See [A semantic model for exact numbers](docs/how-it-works.md#a-semantic-model-for-exact-numbers).

| Change | Edit |
|--------|------|
| New or changed business rule | `rules/<Name>.tmdl`, plus `ref rule <Name>` in `model.tmdl` |
| Description of a rule | `///` line above `rule`. It arrives in the ontology and is returned by `list_ontology_rules`. |
| Extra metadata of a rule | `annotation <Name> = <Value>` lines, written without quotes |
| Description or synonym | `///` line above `entity` or `property`, `synonym 'Name'` lines in `entities/` |
| New table | `tables/<table>.tmdl`, `ref table` in `model.tmdl`, the CSV in `data/` |

Run `./deploy.sh` again to apply a change. To upload only the ontology after a change in `ontology/`, run `.venv/bin/python scripts/update_ontology.py`, which takes about a minute. The file name of a table must match its CSV in `data/`, and the ontology name comes from `ontology/.platform`.

OWL, TTL and RDF files can be imported through the Fabric portal, but only into an empty ontology, and they carry neither data bindings nor rules. That is why TMDL is used here.

## Findings

Checked against a live tenant:

* The Fabric IQ endpoint of the ontology offers three tools: `ask_ontology`, `list_ontology_entities` and `list_ontology_rules`.
* `ask_ontology` applies the ontology rules on the server. The agent never has to be told the rating scheme, the escalation deadline or the change request policy. Only the rules contain "within 1 working day", and the answers include it.
* The model can also call `list_ontology_rules`, but it sometimes sends an empty `entityNames` list. The server rejects that and the whole response fails with HTTP 400. This is a preview quirk, so the prompt does not ask the agent to list rules.
* Multi-word synonyms in TMDL must be quoted, for example `synonym 'Project lead'`.

## Notes

* Fabric IQ and the ontology API are in preview, and tool names can change.
* The script uses the Azure CLI login. Another identity, for example from environment variables, does not see the capacity or the workspace.
* A new Fabric capacity needs a few minutes before the Fabric API lists it. If the script reports "Capacity not found", run `./deploy.sh` again.
* Dates and amounts have real data types (`dateTime`, `double`) that match the lakehouse tables. All other columns are text.
* The ontology agent calculates inexactly when the entities are bound to lakehouse tables (30 instead of 31 days, "exactly 8 weeks" for 59 days), and explicit metrics in the ontology did not change that. The demo therefore binds the ontology to a semantic model with DAX measures, the way the documentation describes it. With the short prompt, 15 of 15 test questions about duration and budget variance were answered correctly. Entities that are bound to a semantic model cannot be projected into the graph, and a Fabric data agent does not work with such an ontology yet.
* The connection uses the Entra token of the signed-in user. The user needs access to the Fabric workspace.
* The old tool "Fabric data agent" is deprecated on 2026-11-06 and is therefore not used.
