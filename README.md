---
title: Construction Project Planning Agent (Demo)
description: Demo of a Microsoft Foundry agent that uses a Fabric ontology (Fabric IQ) to check project data, documents and emails of construction projects
---

## Idea

All data lives as tables in a Fabric lakehouse. A Fabric ontology gives the tables business meaning, and a small semantic model with DAX measures calculates exact numbers. The Foundry agent reaches both only through the Fabric IQ tool: the ontology for meaning and rules, the semantic model for durations and costs.

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
| `ontology/` | The ontology as TMDL files: entities, descriptions, synonyms, relationships, rules, data bindings |
| `semantic_model/` | The semantic model as TMDL files: two tables and the DAX measures for duration, planned cost, budget and variance |
| `extraction/` | Azure Content Understanding analyzer for the PDFs and the known values of the sample documents |
| `scripts/setup.py` | Fabric workspace, lakehouse, tables, ontology, semantic model, Foundry connections, agent |
| `scripts/content_understanding.py` | REST client for Azure Content Understanding, used by `setup.py` to read the terms from the PDFs |
| `scripts/update_ontology.py` | Uploads only the `ontology/` folder, used after a change and by the notebook to restore a rule |
| `scripts/agent_instructions.txt` | Agent prompt: role, which tool answers what, how to query the semantic model |
| `data/` | Four small PDFs and CSV files as test data, amounts in EUR |
| `notebooks/use_case.ipynb` | Bash notebook that plays the use case: an email arrives, is it critical? |
| `docs/how-it-works.md` | Explanation of the solution for readers who know Foundry but not Fabric or ontologies |
| `deploy.sh` | Runs both steps one after the other |

Bicep covers the Azure resources only. Fabric content and the agent live on the data plane, so `scripts/setup.py` creates them through REST and the SDK.

## Run

Prerequisites: Azure CLI (`az login`), Python 3, permission to create resources and role assignments. In the tenant, Fabric must be enabled, the resource provider `Microsoft.Fabric` registered and the Fabric tenant setting "Users can create Ontology (preview) items" switched on.

```bash
./deploy.sh
```

The script ends with a test question to the agent. The F2 capacity costs money while it runs. Pause it in the Azure portal or remove everything with `az group delete -n rg-projplan-demo`.

## Notebook

`notebooks/use_case.ipynb` asks the deployed agent about two incoming emails through the Foundry Responses API. Section 5 then follows the last call through Foundry and Fabric: the agent configuration, the trace in Application Insights, the ontology configuration and the SQL that Fabric ran. Running the whole notebook takes about eight minutes, because the trace and the Fabric query log appear a few minutes after the call.

It needs `jq`, `curl`, `column`, `az login`, a running Fabric capacity and the Bash kernel in the project environment. Section 5.5 downloads `sqlcmd` (Linux x64) to `.tools/` on first use.

```bash
.venv/bin/pip install bash_kernel && .venv/bin/python -m bash_kernel.install --sys-prefix
```

In VS Code select the kernel **Bash** (`.venv/share/jupyter/kernels/bash`). Every code cell must show **Shell Script**.

## Ontology files

`ontology/` holds the Fabric ontology definition format (TMDL), the same files that the Fabric REST API and Git integration use. `setup.py` uploads the folder unchanged and only fills two placeholders in `expressions.tmdl`: `{{SQL_SERVER}}` and `{{SQL_DATABASE_ID}}`, the SQL endpoint of the lakehouse.

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
* The ontology agent calculates inexactly (30 instead of 31 days, "exactly 8 weeks" for 59 days), and explicit metrics in the ontology did not change that. Calculated numbers therefore come from DAX measures in `semantic_model/`. The agent prompt names the model, its measures and an example query, because without that the model often gave up after an empty `ValueSearch`. With the prompt, 30 of 30 test questions about duration and budget variance were answered correctly. This depends on the prompt and the language model that writes the DAX.
* The connection uses the Entra token of the signed-in user. The user needs access to the Fabric workspace.
* The old tool "Fabric data agent" is deprecated on 2026-11-06 and is therefore not used.
