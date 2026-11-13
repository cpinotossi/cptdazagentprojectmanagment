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
* Business rules: email criticality including escalation within 1 working day, and change requests outside the specification

`EmailCriticality` keeps the rating and required escalation in one statement.
Critical emails must be escalated to the responsible project manager within
1 working day; watch and not-critical emails need no escalation. This replaces
the separate `EscalationPolicy` rule. The current agent has no notification tool:
it reports the required action, not a completed delivery.

For absence and office-closure notices, the email-criticality rule requires the
resolved period and project start/end dates, including years, to be stated in the
explanation together with any overlap. A sender's claim of no impact does not
replace that comparison. Other email types do not require this specific time-period
explanation.

## Contents

| Path | Purpose |
|------|---------|
| `infra/main.bicep` | Foundry account, project, chat and embedding model, Fabric capacity F2, Application Insights for tracing, role assignments |
| `ontology/` | The ontology as TMDL files: entities, descriptions, synonyms, relationships, rules, and the tables bound to the semantic model |
| `semantic_model/` | The semantic model as TMDL files: ten tables over the lakehouse, their relationships and the DAX measures for duration, planned cost, budget and variance |
| `extraction/` | Azure Content Understanding analyzer for the PDFs and the known values of the sample documents |
| `scripts/setup.py` | Fabric workspace, lakehouse, tables, semantic model and ontology; calls `setup_agent.py` with the resulting IDs |
| `scripts/import_fabric.py` | Independent PDF, CSV, semantic model and ontology imports using the shared setup functions |
| `scripts/semantic_refresh.py` | Direct Lake refresh polling, semantic queries and complete imported-row verification |
| `scripts/create_demo_pdf.py` | Renders editable plain text as a simple demo PDF |
| `scripts/setup_agent.py` | Fabric IQ connection and Foundry agent version, called by `setup.py`; no inference request |
| `scripts/setup_baseline_agent.py` | Separate tool-free Foundry agent with static data records and document contents, without TMDL definitions |
| `scripts/evaluate_agent.py` | Registers the test dataset and runs Foundry Groundedness against a pinned agent version |
| `scripts/evaluate_business.py` | Runs full-rubric business evaluation of live agents or recorded responses in Foundry |
| `scripts/manual_agent_test.py` | Direct agent calls and editable human reviews of business correctness, completeness and critical errors, without a Foundry evaluator |
| `scripts/content_understanding.py` | REST client for Azure Content Understanding, used by `setup.py` to read the terms from the PDFs |
| `scripts/update_ontology.py` | Uploads only the `ontology/` folder, used after a change and by the notebook to restore a rule |
| `scripts/agent_instructions.txt` | Short agent prompt: role, tool usage, no invented data |
| `data/` | Five small PDFs (including a mislabeled routing counterexample) and CSV files as test data, amounts in EUR |
| `notebooks/use_case.ipynb` | Bash notebook that plays the use case: an email arrives, is it critical? |
| `notebooks/request_walkthrough.ipynb` | Bash notebook that follows one email through every layer, with a growing diagram, the configuration, the code and live evidence per chapter |
| `notebooks/project_synonym_test.ipynb` | Bash notebook with a temporary negative control, then restores the permanent Project synonym `qwert` |
| `notebooks/business_b001.ipynb` | Detailed B-001 walkthrough with individual requests, instructions and Foundry judgments |
| `notebooks/business_all_tests.ipynb` | Compact six-case comparison with separate fixed-file capture and Foundry judging |
| `notebooks/import_updates.ipynb` | Bash notebook for editing and updating one demo PDF, with optional independent metadata and CSV imports |
| `docs/how-it-works.md` | Explanation of the solution for readers who know Foundry but not Fabric or ontologies |
| `docs/configuration-reference.md` | Every setting of the current setup, file by file, with Microsoft references, portal deep links, strengths and weaknesses |
| `docs/microsoft-guidance.md` | Where the demo follows the Microsoft documentation, where it goes beyond it, and what stays open |
| `deploy.sh` | Runs both steps one after the other |

Bicep covers the Azure resources, including the disabled Consumption workflow
definition. Fabric content and agent versions are configured separately.
[setup.py](scripts/setup.py) creates the Fabric content, then calls
[setup_agent.py](scripts/setup_agent.py) to create the connection and agent through REST and the SDK.

For the separate, not-yet-active Logic Apps orchestration preparation, see
[Logic Apps setup and deployment](docs/logic-apps-consumption.md).
The main Bicep template includes the Consumption module by default. A separate
module-only deployment can update it without changing Fabric content or agents.

## Run

Prerequisites: Azure CLI (`az login`), Python 3, permission to create resources and role assignments. In the tenant, Fabric must be enabled, the resource provider `Microsoft.Fabric` registered and the Fabric tenant setting "Users can create Ontology (preview) items" switched on.

```bash
./deploy.sh
```

The script creates the agent without sending a test question. The F2 capacity costs money while it runs. Pause it in the Azure portal or remove everything with `az group delete -n rg-projplan-demo`. F2 is enough for the agent. If you also work in the portal at the same time, it can report that the capacity limit is exceeded. Deploy with `FABRIC_SKU=F4 ./deploy.sh` then, because a deployment without the variable sets the size back to F2.

### Independent imports and refresh

After initial provisioning, [import_fabric.py](scripts/import_fabric.py) lets you
update one layer without redeploying infrastructure or creating an agent version:

```bash
# Requires the same FOUNDRY_ACCOUNT_NAME, FOUNDRY_MODEL_NAME and
# FOUNDRY_EMBEDDING_NAME as deploy.sh; the notebook reads them from deployment outputs.

# Analyze and replace the complete document table from all local PDFs.
.venv/bin/python scripts/import_fabric.py --target pdfs

# Analyze only this PDF and replace or add its document row, preserving other rows.
.venv/bin/python scripts/import_fabric.py --target pdfs --file data/pdfs/P-200_offer.pdf

# Replace one CSV table, without calling Content Understanding.
.venv/bin/python scripts/import_fabric.py --target csv --table emails

# Import all nine CSV tables, without processing PDFs.
.venv/bin/python scripts/import_fabric.py --target csv

# Publish only the semantic model definition, sync SQL metadata and refresh the model.
.venv/bin/python scripts/import_fabric.py --target semantic

# Publish only the ontology definition; no data import or model publication.
.venv/bin/python scripts/import_fabric.py --target ontology

# Inspect visible data without publishing or refreshing anything.
.venv/bin/python scripts/import_fabric.py --target status --doc-id P-200_offer
```

Only PDF actions require the CU environment variables. All actions require
`az login`, the existing `ProjectPlanning` workspace and the appropriate Fabric
permissions. Repeat `--table` to select several CSV tables.

All data actions share the same upload, table-load and verification functions.
They validate all selected CSV headers before the first upload and wait for each
Fabric load operation. Then they explicitly refresh the Direct Lake model
(framing), wait for that exact refresh to complete and query the imported tables
through the semantic model. The comparison checks all column values and row
multiplicities, not just row counts. A stale result triggers another framing
attempt within the 900-second verification window; API errors, failed jobs and
timeouts remain errors. Success is recorded in
`extraction/outputs/import_report.json` only after verification.

The full [setup.py](scripts/setup.py) workflow uses these same functions. It also
requests SQL endpoint metadata synchronization when publishing the semantic model.
If Fabric reports `NotRun` for a previously synchronized table, the script prints
that status and its last successful synchronization timestamp, without claiming a
new sync occurred. A missing prior sync or a per-table failure stops publication;
model refresh and table verification remain required afterward. The
ontology remains bound to that model; a graph projection is neither created nor
refreshed. Semantic-model-bound entities aren't eligible for graph materialization.

Single-PDF updates read the current document rows from a freshly refreshed model
and merge by `DocId`, derived from the filename stem. The combined CSV is saved
locally before the table is overwritten. This preserves existing rows that aren't
selected, including documents absent from the local folder. It is not a
transactional row-level upsert: don't run concurrent imports or other writers
against the document table. Renaming a PDF creates a different document ID and
doesn't delete the old row. Use a complete PDF import when you intentionally want
the table to match the local folder, including removals.

Snapshot merging and verification are intended for this small demo, not large
tables. Queries that reach the Power BI row or value limits, or return partial
results, are rejected rather than used as a replacement table. Multiple table
loads aren't one transaction; a failed run can leave earlier tables updated.

References: [Direct Lake framing](https://learn.microsoft.com/fabric/fundamentals/direct-lake-how-it-works#framing),
[asynchronous refresh](https://learn.microsoft.com/power-bi/connect-data/asynchronous-refresh)
and [SQL metadata sync](https://learn.microsoft.com/fabric/data-engineering/sql-analytics-endpoint-metadata-sync).

### Edit and update a document in Bash

Open [import_updates.ipynb](notebooks/import_updates.ipynb) with the **Bash** kernel
from `.venv`. The notebook reads the CU configuration from the existing Azure
deployment, captures the current document rows and lets you edit the demo's text
in a Bash heredoc. It renders that text to
[P-200_offer.pdf](data/pdfs/P-200_offer.pdf) using
[create_demo_pdf.py](scripts/create_demo_pdf.py), then imports only that PDF.
The editable source is also saved in
[P-200_offer.txt](data/pdf_sources/P-200_offer.txt).

After refresh, the notebook displays the updated record and checks that every
other document remains unchanged. The misleading offer heading must still yield
`DocType = Contract`. Change the revision text to demonstrate a second update.
The renderer supports basic Windows-1252 text and page wrapping, not arbitrary
PDF layout preservation, signatures or forms.

CSV, semantic model and ontology publication have separate notebook cells. They
are disabled by default so **Run All** doesn't republish definitions or overwrite
other tables. Enable only the required switch in the configuration cell.

### Local Content Understanding outputs

Before uploading data to OneLake, [setup.py](scripts/setup.py) saves the analyzed
results returned by Content Understanding under `extraction/outputs/`:

* One `<PDF-stem>.json` per PDF, containing the full analysis result with routed
  content, its category and analyzer ID, Markdown, extracted fields and confidence values
* `documents.csv`, containing the exact UTF-8 bytes uploaded as the document table

The directory is created automatically and excluded from Git. Each import
overwrites the files for the PDFs it processes and the combined CSV. This is a
local copy of the extraction results, not a cache: subsequent imports still call
Content Understanding. If a local write fails, the import stops before any
OneLake upload starts.

### Content-based document routing

[document_router.json](extraction/document_router.json) defines the native Content
Understanding router `projectDocumentRouter`. It classifies each complete PDF by
its content as `Specification`, `Offer`, `Contract` or `Other`, without splitting
it into multiple documents (`enableSegment: false`). The three supported types
route to `projectDocumentTerms`, whose shared field schema remains defined in
[document_terms.json](extraction/document_terms.json). Sharing the extractor keeps
the same document-table columns for all three types.

The router returns only routed content (`omitContent: true`). The import uses its
`category` as `DocType`, not the filename or heading. Project IDs still come from
the filename prefix before the first underscore; the whole filename stem remains
the document ID. Missing, unsupported or multiple routed documents stop the import
before any OneLake upload. The raw result is saved locally even when validation
rejects it; there is no fallback to a filename-derived document type.

[P-200_offer.pdf](data/pdfs/P-200_offer.pdf) is a synthetic counterexample. Both its
filename and heading say "Offer", but its body is a binding construction agreement
between signed parties. It repeats the existing P-200 budget, approval threshold
and contract term without adding conflicting business terms. Its expected
`DocType` is `Contract`, while `Title` deliberately retains the misleading heading.
The PDF is included in the normal import, resulting in five document rows.

Routing configuration follows Microsoft's
[classification and routing guide](https://learn.microsoft.com/azure/ai-services/content-understanding/how-to/classification-content-understanding-studio).
Classification is model-based and can still be wrong; the counterexample verifies
this case, not every possible ambiguous document.

## Baseline without Fabric IQ

[setup_baseline_agent.py](scripts/setup_baseline_agent.py) creates the separate
Foundry agent `project-planning-baseline`. It uses the model deployment and
generation settings of a pinned version of `project-planning-agent`, but has no
tools, skills, Fabric IQ connection or live data access at inference time.
The existing agent and Fabric items are not changed.
The baseline instructions focus on the supplied evidence, citations and missing
information. They do not enumerate unavailable tools or services; the agent
definition enforces the empty tool list.

```bash
export FOUNDRY_PROJECT_ENDPOINT="$(az deployment group show \
  -g rg-projplan-demo -n main \
  --query properties.outputs.foundryProjectEndpoint.value -o tsv)"
.venv/bin/python scripts/setup_baseline_agent.py
```

The setup reads all ten tables from the existing semantic model, including full
document contents and extracted contract fields. Only data records and capture
metadata are embedded as static JSON in the baseline's instructions. No TMDL,
ontology rules, synonyms, entity/relationship definitions or DAX formulas are
included. Contract clauses in the original document contents remain available.
It does not include evaluation questions, expected answers or precomputed measure
results. The model must infer connections and calculate results itself.
Azure CLI access to Foundry and Read/Build access to the semantic model are needed
during setup, not during baseline inference. The source Fabric capacity must be
active during live capture; the baseline does not need it afterward.

The demo workspace and semantic model IDs are explicit constants in
the script. By default, each execution captures fresh data and creates a new baseline agent
version. Use `--reference-version VERSION` to select the Fabric IQ reference
version explicitly. The setup sends no test question.

Basic-Agent version 3 was captured from the current semantic model on
2026-11-13. It contains 2 projects, 14 tasks, 15 dependencies, 2 managers,
5 emails and 5 documents, including `P-200_offer`. Readback verified the
exact data-only instructions and an empty tool list. New comparisons should
pin version 4, which reuses that exact snapshot and the same model settings with
a shorter policy that omits unavailable-tool explanations. Versions 2 and 3
remain historical.

To keep exactly the same data while removing metadata from an earlier snapshot,
pass `--snapshot evaluations/baselines/SNAPSHOT.json`. This reads only the stored
table records and capture metadata, discards any old TMDL sections and does not
access Fabric. The original snapshot and agent version are retained.
Baseline version 1 contained TMDL knowledge; use the new data-only version for
the comparison without ontology knowledge.

The exact instructions, source IDs, capture interval, reference agent version,
baseline version, snapshot provenance and instruction SHA-256 are saved under
`evaluations/baselines/`, which is
gitignored. Keep that file for reproducibility; it contains the source records.
Earlier snapshots have been moved to
[archive/baselines/](evaluations/archive/baselines/); pass their archived path
explicitly when reproducing an older baseline.
Reads are sequential, not an atomic cross-table snapshot. Avoid source changes
during capture and comparison.

For comparison, ask both pinned agent versions the same questions in fresh
conversations, using the same response-generation options. Compare facts, numeric
accuracy, rule application, citations, latency and token usage. The baseline has
the source documents but not the ontology's additional policies or synonyms.
Questions using ontology-only vocabulary or escalation policies therefore test
missing knowledge as well as reasoning. Report document questions separately from
ontology-policy questions. This tests the complete Fabric IQ setup against a
data-only context, not the execution engine or ontology in isolation.
Static context lacks live updates and DAX execution,
and its prompt size grows with the data. A successful answer is not evidence that
the baseline queried Fabric.

The existing evaluation script can target either agent with `--agent` and
`--agent-version`; keep the test dataset, judge model and scoring criteria the
same. Groundedness alone does not measure correctness or completeness.

## Business correctness and completeness tests

[business_tests.json](evaluations/business_tests.json) is the active reference
dataset with six German inputs, not stored source emails:

1. Email about a permit assumption contradicted despite claimed schedule buffer
2. Email about an office closure entirely before the project starts
3. Question comparing budget forecasts at exactly 10 percent and 11 percent overrun
4. Question about additional scope and an 80-hour effort estimate
5. Fictional supplier news: one product's delivery stop conflicts with P-100's delivery but not P-200's later delivery
6. Existing `qwert` synonym regression: list both projects with their IDs and names

Each case includes the agent input (`query`), reference evidence (`context`),
an expected answer (`ground_truth`), mandatory criteria, critical errors and source
references. Numeric criteria include exact reference values. Only `query` goes to
the agent. The references and ontology policies go to the Foundry judge.
The supplier article establishes a delivery-date conflict within the fictional
scenario, not an actual construction delay or project-completion impact. It does
not extend email-only escalation rules to news.
Unlike the first five held-out cases, B-006 reuses the established synonym
question. It tests ontology vocabulary in addition to shared project records.
The Basic-Agent has those records but not the synonym; an honest refusal is a
missing lookup result, not automatically a hallucination. The test does not
modify the ontology or inject the mapping into either agent's instructions.

### Supplier deliveries in B-005

The fictional supplier `S-100`, BauDaemm GmbH, supplies material `MAT-035`,
mineral wool insulation board MW-035, to both projects. The source records are
[suppliers.csv](data/suppliers.csv), [materials.csv](data/materials.csv) and
[material_deliveries.csv](data/material_deliveries.csv). Each delivery belongs
to an existing work package in [tasks.csv](data/tasks.csv), which identifies its
project and responsible project manager. These work packages have start and end
dates; they are not new point-in-time milestone entities.

| Delivery | Project | Work package | Planned delivery | Result for the reported stop |
|----------|---------|--------------|------------------|------------------------------|
| DL-100 | P-100 | T-104, Facade and roof | 2027-08-02 | Inside 2027-08-01 through 2027-09-30 |
| DL-200 | P-200 | T-205, Facade and roof | 2028-01-03 | Outside the reported interval |

[SupplyDisruptionPolicy](ontology/rules/SupplyDisruptionPolicy.tmdl) matches
supplier and product, checks outstanding delivery dates against the inclusive
stop interval, and follows delivery -> task -> project -> project manager.
It distinguishes supply exposure from confirmed downstream delays, costs or
penalties. A date outside this interval is not a guarantee against future
shortages. The test asks for stock, replacement sourcing, supplier confirmation,
task dependencies and schedule buffers before drawing downstream conclusions.
The article remains held-out test input, not a stored source record.

Both managers and all relevant delivery records are included in the judge's
reference context. The prior regional-news reference is preserved in
[business_tests_before_supplier_scenario.json](evaluations/archive/business_tests_before_supplier_scenario.json).
Historical results cannot be compared directly to the replacement B-005.
Refresh the Basic agent's data snapshot after publishing the new tables so both
agents receive the same records. The baseline policy and the Fabric IQ agent's
instructions need no supplier-specific changes. Saved six-table baseline
snapshots can still be replayed for historical tests.

The many-to-many relationship `Supplier Offers Material` is backed by
[supplier_materials.csv](data/supplier_materials.csv). It records a supplier's
assortment independently of project deliveries. Both `S-100`, BauDaemm GmbH, and
`S-200`, IsolierPartner GmbH, list `MAT-035`. S-200 has no existing project
delivery, but is a recorded alternative source for the same product.
The disruption policy proactively names such alternatives when a delivery
conflicts with a report and also supports direct questions about alternatives.
An assortment entry does not prove stock or delivery capacity. Availability,
required quantity, delivery by the needed date, price and procurement approval
must be confirmed before a substitution; no order is changed by this analysis.
No other recorded candidate means no recorded alternative, not a guessed vendor.

B-005 now also asks for the alternative supplier and those confirmation steps.
The previous supplier-only reference is preserved in
[business_tests_before_alternative_sourcing.json](evaluations/archive/business_tests_before_alternative_sourcing.json).
New Basic snapshots include all ten tables. Saved six- and nine-table snapshots
remain usable for historical tests.

Live checks verified both explicit alternative-source questions and, after
clarifying the delivery and relationship descriptions, proactive naming of
S-200 without an alternatives question. Agent instructions stayed unchanged.
The six-case notebook run before that description clarification scored 5/6
for Fabric IQ v8 and 1/6 for Basic v6. Its B-005 results remain native FAILs.
A focused B-005 run after the clarification correctly named S-200 and qualified
its availability; the native judge still returned FAIL because the answer did
not explicitly mention that the email-only one-working-day rule does not apply.
The answer invented no such deadline. These judgments were not replaced with
local grades or changed evaluator criteria.

### Reference exports

The previous ten-case dataset is retained in
[archive/](evaluations/archive/), and historical evaluation reports remain unchanged.
Their Groundedness scores are not comparable to business-correctness outcomes.
[business_tests.jsonl](evaluations/business_tests.jsonl) contains all six cases
as a Foundry-compatible projection. Regenerate it after reference changes:

```bash
.venv/bin/python scripts/manual_agent_test.py export
```

### Inspect B-001 in the Bash notebook

Open [business_b001.ipynb](notebooks/business_b001.ipynb) and select the
environment's Bash kernel. Code cells use VS Code's `shellscript` language ID.
The notebook tests only B-001: the email about P-100's delayed building permit.
It uses the existing Fabric IQ agent version 8 and Basic-Agent version 6.
Change the pinned names and versions
for other deployments. Azure CLI login, Foundry access and an already active
Fabric capacity are required for live comparison. The notebook never resumes or
pauses capacity, provisions resources or changes agents.

Registration, agent inference and judging are separate cells:

The first code cell defines all endpoint URLs and fixed file paths. No Bash
functions hide requests: each agent cell contains its `az rest` call directly
and prints the full URL. Evaluation cells use `--show-endpoints` to print the
actual SDK request methods and URLs, including polling, without headers or tokens.
Every `az rest` response is captured with `--output json` in a Bash variable:
`EVALUATOR_RESPONSE`, `EVALUATOR_UPDATE_RESPONSE`, `IQ_RESPONSE` or
`BASIC_RESPONSE`. The same JSON is also saved to its fixed file. Inspect a
response directly with, for example, `jq '.id, .status' <<< "$IQ_RESPONSE"`.

Run all cells from top to bottom. There are no conditional branches, caching
checks or update switches.

1. Both registration cells upload the local JSON definition with a direct `POST`.
   Each publishes a new version of the same evaluator and overwrites the fixed
   `EVALUATOR_FILE`; historical versions are not modified. A complete notebook
   run therefore creates two versions. Both evaluations use the second version.
   The registration body includes `display_name` and `description`, which populate
   the Foundry catalog columns; the technical resource `name` alone does not.
2. One `az rest` cell sends B-001 only to Fabric IQ; a separate cell sends it only
   to Basic. Each saves the full Responses API JSON and its capture provenance.
   Each execution makes one new agent request and overwrites that agent's fixed
   files. There is no automatic retry.
3. `evaluate_business.py --captured-responses ... --case-id B-001
   --evaluator-version ...` submits exactly one saved answer per cell to its own
   live Foundry evaluation with the pinned custom evaluator and `gpt-4.1` judge.
   Fabric IQ and Basic have independent evaluation cells and report URLs.
   This Python helper handles schema mapping, polling and validation; it neither
   calls the agents nor registers another evaluator in this mode.
   `--result-file` writes to the fixed result path configured at the top.

Only each case's `query` reaches the tested agent. The judge receives its generated
answer plus the full reference context, expected answer, mandatory criteria and
critical errors. Agent inference and judge inference both incur charges.
There is no separate Groundedness run or required human grading.

Each capture cell displays only its agent's answer. Each evaluation cell displays
only that agent's Foundry pass/fail decision, reason, report URL and result counts.
Setup and registration cells run no tests. There is no combined two-agent test
cell. Two additional display cells show each agent's full `instructions` from
`IQ_RESPONSE` and `BASIC_RESPONSE`, including Basic's embedded static snapshot,
without new API calls or agent changes.
Nothing grades text locally or replaces a cloud decision. `dimension` values
remain custom labels in the supplied rubric, not built-in Foundry metrics.
Known judge misreadings and incomplete reference context can still affect results.
One case does not establish general superiority.

Local audit copies are written to the gitignored
`evaluations/notebook_runs/p100-permit/` directory. `IQ_RESPONSE_FILE` and
`BASIC_RESPONSE_FILE` identify the fixed raw agent-answer files.
`IQ_RESULT_FILE` and `BASIC_RESULT_FILE` identify the fixed evaluation files.
Each agent cell refreshes its answer in the same fixed files. Each execution of a
judge cell starts a new evaluation of the saved capture and updates only that
agent's fixed result file, rather than creating a timestamped file. Older files
outside these fixed paths are not deleted. Displaying results calls neither model.
If polling times out, use the printed result path with
`evaluate_business.py --endpoint ... --resume <RESULT.json>` to continue the
existing run. Historical captures under
[archive/manual_runs/](evaluations/archive/manual_runs/) and the
optional manual-review mode remain available, but this notebook does not require
or use human grading.
This explicit capture-then-evaluate path avoids the tool invocation failures
observed with Fabric IQ through Foundry's agent-target-completions path.

### Run all six business cases

Open [business_all_tests.ipynb](notebooks/business_all_tests.ipynb) with the Bash
kernel. Seven code cells configure the run, publish and pin its reference, capture each agent's six answers,
judge each saved set and display the comparison. Agent versions are pinned to
Fabric IQ v8 and Basic v6; custom evaluator version 6 is reused without creating
another version.

Capture cells use `evaluate_business.py --agent NAME --agent-version VERSION
--capture-only --reference-asset FILE --output-dir DIRECTORY`. Each execution makes one fresh request
per dataset question, sends only `query` in a fresh context and overwrites fixed
`B-001.response.json` / `B-001.capture.json` files through B-006. Raw responses
retain tool details; captures add agent identity, reference hash and provenance.
There are no automatic agent-request retries.

Fabric IQ v8 requires the first `ask_ontology.request` to copy the complete user
message, including the entire email or article body. Business captures verify
this against the actual tool arguments, ignoring whitespace differences only.
Missing or shortened handoffs are saved as `status: error` with the raw response
and are rejected again before judging, even if Azure reports `completed`.
The B-001 notebook checks its direct CLI capture before displaying the answer.
Old non-verbatim captures must be recaptured rather than silently reused.

This is transport validation, not local business grading. The managed Foundry
tool call has already executed when its trace becomes available; the check
detects a violation and blocks evaluation, rather than repairing or retrying it.
Portal-only conversations receive the new instructions but not this local
capture gate. A successful individual run is not proof of deterministic routing.
Version 8 preserves version 7's model, temperature and tool configuration.

Judge cells use `--captured-responses DIRECTORY/*.capture.json
--evaluator-version 6 --result-file FILE`, without `--case-id`. This requires
exactly the complete dataset for each supplied agent and rejects duplicate,
failed or mismatched captures before submitting an evaluation. Supplying
`--case-id B-001` retains the single-case workflow. Judging never calls the agents.

All suite files are under the separate, gitignored
`evaluations/notebook_runs/all-business-tests/` directory:

* `fabric-iq/` and `basic/`: six raw responses and six captures per agent
* `fabric-iq.evaluation.json` and `basic.evaluation.json`: one report per agent,
  each containing six Foundry decisions
* `comparison.json`: twelve case decisions with their agent identities and reasons
* `reference.asset.json`: immutable Foundry reference ID, version and SHA-256
* `reference.asset.data.json`: verified download of that reference version

The final cell displays one row per case with Fabric IQ and Basic PASS/FAIL
side by side, followed by totals and each run's portal report URL.
The agents keep separate evaluation IDs and run IDs. Portal entries start with
`Fabric IQ` or `Basic`, followed by the pinned agent version, case scope,
evaluator version and reference hash. Matching `comparison_id` metadata links
the two independent runs without mixing their answers.

The overview shown under Foundry **Evaluations** lists evaluation definitions,
not per-case outcomes. Open an entry and its last run to inspect its metrics
and individual decisions. Use the notebook's table for a direct cross-agent
comparison; the saved reports retain both judges' reasons.

Re-execute only the judge cells to evaluate saved answers again. Capture cells
refresh answers; judge cells refresh reports; neither touches the B-001 files.
The notebook links the inputs and outputs directly below every code cell.
One full run makes twelve agent requests and two evaluation runs containing twelve
individual judgments. The runner's other CLI modes remain available.

### Local references and Foundry datasets

Agents and judges already execute in Foundry. Local test definitions and saved
answers do not mean that evaluation happens locally: the runner sends the
generated answers and full reference fields to Foundry's evaluation API.

The runner uploads the complete judge input as JSONL and submits its registered
`file_id`, not inline `file_content`. Stable names are
`project-planning-business-reference` for the reviewed source definition and
`project-planning-business-inputs-AGENT_NAME` for each agent's generated inputs.
Versions use the first 16 hexadecimal characters of the file's SHA-256.
Identical files reuse the existing version; changed answers or references get
new versions. Neither agent identity nor calibration labels are judge fields.

Both notebooks publish the Git reference through `--publish-reference`, saving
its pinned ID/version/hash in `reference.asset.json`. Capture and judge steps
use `--reference-asset` to download that exact Foundry version and verify its
ID, endpoint and complete SHA-256 before using it. Local files are audit copies,
not an independently selected reference. To use an older version, retain its
manifest and matching captures instead of republishing the current source.

Earlier inline runs have accumulated timestamped `eval-data-*` assets. These are run inputs,
not additional independent test suites. Keep referenced historical inputs when
their reports must remain reproducible; do not delete them without checking run
references and retention requirements.

The runner also reuses each agent's evaluation definition when its pinned
reference, case scope, evaluator and judge settings match. Every judging
execution still creates a separate run. A questions-only dataset cannot replace
the answers being judged.

Foundry supports both inline `file_content` and registered `file_id` sources;
see [Evaluate existing datasets](https://learn.microsoft.com/en-us/azure/foundry/observability/how-to/cloud-evaluation-datasets).
Historical assets and evaluation entries are not renamed or deleted.

### Evaluation file layout

The B-001 walkthrough needs
[business_tests.json](evaluations/business_tests.json),
[business_correctness.json](evaluations/evaluators/business_correctness.json)
and the fixed files directly under
[p100-permit/](evaluations/notebook_runs/p100-permit/).

Optional CLI modes and automated tests still use
[business_tests.jsonl](evaluations/business_tests.jsonl),
[business_judge_controls.json](evaluations/business_judge_controls.json),
[business_tests_v2.json](evaluations/archive/business_tests_v2.json)
and the two historical regression fixtures retained in
[runs/](evaluations/runs/).
The exact five-case reference before B-006 was added is preserved as
[business_tests_v3.json](evaluations/archive/business_tests_v3.json). Adding a
case changes the dataset hash; refresh suite captures before judging with the
new reference. Historical captures can use their matching archived reference.

Other historical run results, manual captures, baseline snapshots and previous
notebook runs are preserved under [archive/](evaluations/archive/), grouped by
their original purpose. Earlier timestamped P-100 judge runs are under
[archive/notebook_runs/p100-permit/judges/](evaluations/archive/notebook_runs/p100-permit/judges/).
Their recorded provenance retains the paths used at capture time. Nothing was
deleted. Archived generated snapshots and captures remain gitignored.

### Foundry rubric evaluator trial

`evaluate_business.py --evaluator-kind rubric` uses a Foundry-managed
`type: rubric` evaluator, not our custom judge prompt. Version 1 of
`project-planning-business-rubric` has five equally weighted dimensions:
`shared_data`, `ontology_policy`, `uncertainty`, `critical_errors` and
`general_quality`. Non-applicable dimensions are excluded by Foundry.
The normalized pass threshold is the documented default, 0.5.

The trial replays the exact ten answers from the latest valid notebook comparison:

```bash
.venv/bin/python scripts/evaluate_business.py \
  --endpoint "$FOUNDRY_PROJECT_ENDPOINT" \
  --evaluator-kind rubric --evaluator-version 1 --judge-model gpt-4.1 \
  --compare-runs \
  evaluations/archive/notebook_runs/20261113T155608050335277Z/fabric-iq/evalrun_da75930c6a1d46aa9299df1ed0297de6.json \
  evaluations/archive/notebook_runs/20261113T155608050335277Z/basic/evalrun_794c3a7dadb2425fa7312bd29a33d90d.json
```

No agent is called again. Only the Foundry judge incurs inference charges.
The original question remains unchanged. A separate `rubric_query` reference
packet supplies the original question, context, expected answer and criteria
only to the evaluator; agent identity is not mapped to the judge.

The [saved trial](evaluations/archive/runs/evalrun_b8f3fbe5241c426e832d9f20cfc2fa00.json)
contains all ten cloud judgments, per-dimension scores and reasons:

| Agent | Mean rubric score | Foundry passes |
| --- | --- | --- |
| Fabric-IQ-Agent v7 | 0.86 | 4/5 |
| Basic-Agent v3 | 0.68 | 3/5 |

These are different scoring rules from the binary evaluator's 3/5 and 2/5 on the
same answers. For example, Basic-Agent B-005 passes with 0.75 while receiving 4/5,
not full marks, in every dimension. A pass in this weighted trial is not equivalent
to satisfying every mandatory criterion without error. More passes do not prove more accurate
grading. Reference-context gaps and LLM judgment errors remain possible.
The notebook's default evaluator is unchanged for this trial.

See Microsoft's [rubric evaluator documentation](https://learn.microsoft.com/azure/foundry/concepts/evaluation-evaluators/rubric-evaluators)
and [manual-authoring example](https://github.com/Azure/azure-sdk-for-python/blob/main/sdk/ai/azure-ai-projects/samples/evaluations/sample_rubric_evaluator_manual.py).

### Foundry business evaluator regression

[business_correctness.json](evaluations/evaluators/business_correctness.json)
contains the complete Foundry definition of the custom prompt-based binary
evaluator, `project-planning-business-correctness`: judge prompt, input schema,
initialization parameters and metric. Edit this JSON file to change the evaluator;
there is no separate Python evaluator-definition script.

The runtime reads this file and submits the parsed definition unchanged through
`project.evaluators.create_version`. It derives input-field mappings from the JSON
schema rather than maintaining a second field list. Registration metadata such as
the evaluator name and definition hash is added by the runner. Without
`--evaluator-version`, a run registers a new version; a pinned version is reused
only if its persisted definition matches the local JSON. Editing the file alone
does not update an existing evaluator in Foundry.

The existing [evaluate_business.py](scripts/evaluate_business.py) command remains
the entry point with unchanged arguments and notebook calls. It delegates to:

* [business_rubric_evaluator.py](scripts/business_rubric_evaluator.py) for the separate
  weighted rubric definition
* [business_evaluation_data.py](scripts/business_evaluation_data.py) for recorded
  answers, comparison cases, calibration inputs and provenance checks
* [business_evaluation_runtime.py](scripts/business_evaluation_runtime.py) for
  evaluator registration, live response capture, data mapping and Foundry runs
* [business_evaluation_results.py](scripts/business_evaluation_results.py) for
  validating and summarizing Foundry decisions

The runtime reuses response capture from
[manual_agent_test.py](scripts/manual_agent_test.py) and run polling and persistence
from [evaluate_agent.py](scripts/evaluate_agent.py). Evaluator definitions and
workflow helpers each have a single source; splitting them doesn't change prompts,
rubric dimensions, scoring, output formats or cloud configuration.

The judge
receives the question, candidate response, reference context, expected answer,
mandatory criteria and critical-error definitions. Its instructions require every
mandatory fact to be correct, no critical error and no additional unsupported
business claim. Missing facts and contradictory deadlines must fail; correct facts
cannot offset them. The judge's reason lists criterion decisions and answer evidence.
This remains an LLM judgment, not a guarantee of correct grading.

The regression replays the actual B-001 baseline response from the historical
Foundry run, including its invented four-week escalation deadline. Three explicit
synthetic controls in
[business_judge_controls.json](evaluations/business_judge_controls.json) test a
complete correct answer, an omitted escalation deadline and a response containing
both the correct and an incompatible deadline. Expected results are fail, pass,
fail and fail. These are evaluator calibration cases, not a new agent benchmark.
Reference changes stop replay so stale evidence cannot be mixed silently.
The pre-consolidation reference is archived for these historical responses;
fresh comparisons use the active dataset with the merged email policy.

```bash
.venv/bin/python scripts/evaluate_business.py \
  --endpoint "$FOUNDRY_PROJECT_ENDPOINT" \
  --dataset evaluations/archive/business_tests_v2.json
```

Registration, judgment and per-case results happen in Foundry. The script only
submits existing texts, waits, saves a local audit copy and checks whether the
Foundry decisions match the known calibration labels. Those labels are not sent
to the judge. No tested agent is called or changed, and Fabric is not accessed.
Only judge inference incurs charges. Each invocation registers a new version and
starts a new run; `--resume evaluations/runs/<RUN_ID>.json` resumes polling without
creating another evaluator or evaluation. The evaluator definition is verified by
readback, and its version, prompt hash, source hashes and report URL are retained.
A nonzero exit indicates failed calibration or an execution error; expected rejection
of the deliberately wrong responses is successful calibration.

Custom evaluators are a Foundry preview feature. Registration explicitly sends the
service-required `Foundry-Features: Evaluations=V1Preview` opt-in header.
The verified B-001 calibration run using evaluator version 2 and `gpt-4.1` rejected
all three invalid responses and accepted the complete control. This verifies those
four cases only, not universal judge reliability or the full five-case agent benchmark.
See
[prompt-based custom evaluators](https://learn.microsoft.com/en-us/azure/foundry/concepts/evaluation-evaluators/custom-evaluators#prompt-based-evaluators).
The existing manual notebook and Groundedness evaluator remain separate and unchanged.

To compare all five previously recorded answers from each agent using the same
calibrated evaluator, run:

```bash
.venv/bin/python scripts/evaluate_business.py \
  --endpoint "$FOUNDRY_PROJECT_ENDPOINT" --evaluator-version 2 \
  --dataset evaluations/archive/business_tests_v2.json \
  --compare-runs \
  evaluations/runs/evalrun_561e13fcca05413ebdec5422a4cba9ab.json \
  evaluations/runs/evalrun_37a9833521b0411a866a1fb88d56f950.json
```

The script verifies identical source dataset hashes and current per-case references,
replays actual answers without new agent calls and maps only evaluation inputs to
the judge. Agent names and historical Groundedness scores are not supplied to it.
All business decisions are made in Foundry; the local summary only groups those
decisions by the recorded agent and case. A valid low business score is a comparison
result, not an execution error. The full five-case comparison is not a regression
calibration with predetermined expected labels.

### Fresh comparison after consolidating the email policy

The consolidated `EmailCriticality` was published and verified by definition
readback. `EscalationPolicy` is absent; the other 18 definition parts retain their
content, apart from Fabric reordering identical partition annotations.
Five fresh answers from Fabric-IQ-Agent v7 and five from Basic-Agent v2 were
evaluated with the unchanged Foundry business evaluator v2 (`gpt-4.1`).

| Case | Fabric-IQ-Agent | Basic-Agent |
|------|-----------------|-------------|
| B-001 | Pass | Fail |
| B-002 | Pass | Fail |
| B-003 | Pass | Pass |
| B-004 | Pass | Pass |
| B-005 | Fail | Fail |
| Total | 4/5 | 2/5 |

These are raw Foundry judgments, not verified human scores. Fabric IQ now states
both project dates and the nonoverlapping closure period in B-002. In B-001 it
requires escalation within 1 working day; the Basic-Agent instead recommends
5 working days and mixes in the contractual eight-week withdrawal threshold.
The judge still misses omissions and accepts questionable wording: for example,
Fabric IQ labels the B-004 amount as a final price rather than clearly retaining
an estimated amount. Its B-005 rejection rationale also misreads the treatment
of Hamburg. Audit caveats are saved without overriding cloud decisions in the
[comparison result](evaluations/archive/runs/evalrun_a481c22f36bd4070a70b03d7dfdfaa9a.json).

The absence-period requirement was first deployed with this consolidation, so
this run does not isolate the effect of merging the rules. Fabric IQ uses live
data, while Basic-Agent v2 retains its earlier snapshot, without the newly
imported P-200 offer. No notification tool was added and no email was sent.

To replay the new answers with the active reference:

```bash
.venv/bin/python scripts/evaluate_business.py \
  --endpoint "$FOUNDRY_PROJECT_ENDPOINT" --evaluator-version 2 \
  --compare-runs \
  evaluations/archive/runs/evalrun_da4ef9ca40b9485c9c949a96851a0da3.json \
  evaluations/archive/runs/evalrun_290c20ea6a85470190bced666d35212c.json
```

### Current-data comparison with Basic-Agent v3

Basic-Agent v3 adds `P-200_offer` to the version-2 snapshot; the other five
tables and all four existing documents are unchanged. After generating five
fresh answers from each agent, sequential live reads verified that all six
semantic-model tables still matched the version-3 snapshot exactly.
Both agents use `gpt-4.1`; Basic-Agent still has no tools or ontology policies.

The unchanged Foundry business judge v2 returned 2/5 passes for Fabric-IQ-Agent
v7 (B-001, B-002) and 1/5 for Basic-Agent v3 (B-003). These raw judgments do not
establish a ranking: for example, it rejected Basic-Agent B-005 for correctly
naming Anna Becker as P-100's manager because that fact is missing from the
curated per-case context, although it exists in both agents' source data.
The [saved comparison](evaluations/archive/runs/evalrun_f5b1fefb522e47ddb00d14abe6d3a35d.json)
retains the cloud decisions, data-parity verification and review caveats.

```bash
.venv/bin/python scripts/evaluate_business.py \
  --endpoint "$FOUNDRY_PROJECT_ENDPOINT" --evaluator-version 2 \
  --compare-runs \
  evaluations/archive/runs/evalrun_eb4a4cb7b69e46ee84f4a93ab849e53a.json \
  evaluations/archive/runs/evalrun_f2dc41f7d9bf4ae79a0632b7d63cc938.json
```

### Optional Foundry Groundedness evaluation

The existing Foundry evaluation script now defaults to the same six inputs.
It still measures Groundedness only; it does not execute the manual business
rubric. Use it as an additional measurement, not a correctness verdict.

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
`calls[].arguments` shows the question forwarded to a tool. The first
`ask_ontology` request must preserve the complete original input; only subsequent
focused queries may differ. The complete sent requests remain visible in the preparation cells.
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
