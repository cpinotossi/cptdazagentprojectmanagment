---
title: Alignment with Microsoft guidance
description: Where the demo follows the Microsoft documentation for Fabric IQ, Foundry and semantic models, where it goes beyond it, what we measured, and what stays open
---

The demo builds on preview features. Microsoft documents the architecture, but several steps that the demo does by code are described only for the portal. This page lists, for each area, what Microsoft documents, what the demo does and how the two relate. Statements about our own tenant come from tests on 5, 6 and 11 November 2026. Every documentation reference is a Microsoft Learn page unless stated otherwise.

## Summary

| Area | Microsoft documents | The demo | Status |
|------|---------------------|----------|--------|
| Agent to Fabric | Foundry tool `fabric_iq_preview`, connection `RemoteTool` with `UserEntraToken` | Same | Matches |
| Ontology definition format | TMDL through the Fabric REST API | Same | Matches |
| Ontology bound to a semantic model | Semantic models are a supported data source in the portal, and "Generate Ontology" creates entities, bindings and metrics | Same result, written by code | Extends the documentation |
| Partition mode for the binding | `mode: directLake` in the TMDL reference | `mode: directQuery` as the portal writes it | Outside the documented values |
| `ONT_*` partition annotations | Not mentioned in the reference | Six source annotations per partition; adding them fixed Project Instances | Tested portal behavior, see below |
| Rule descriptions | The reference says `getDefinition` does not return them | They are stored and returned | Differs from the documentation, see below |
| Graph and data agent | Not supported with semantic model bindings | Not used with the final setup | Matches |
| Service principal | Not supported for the ontology MCP endpoint | The signed-in user | Matches |
| Semantic model preparation for AI | Prep for AI (AI data schema, verified answers, AI instructions) for data agents | Not configured. A test showed that the ontology endpoint ignores AI instructions | Tested, no effect |
| Publish before use | A Fabric admin must publish each item | No publish step was needed | Open |
| Ontology design guidance | Pages on entity types, relationships and rules | Mostly followed, rules are under-linked | Findings below |

## Metrics from a semantic model

Microsoft describes this path in three places.

* [Define business metrics in ontology](https://learn.microsoft.com/fabric/iq/ontology/how-to-use-metrics): "When you generate an ontology (preview) from a Power BI semantic model, the ontology surfaces the model's DAX measures under the corresponding ontology entities as metrics." A metric "keeps a link to the semantic model and pulls the original DAX expression on demand".
* [What is ontology](https://learn.microsoft.com/fabric/iq/ontology/overview#key-capabilities): "The source semantic model remains the owner of the DAX expression, while ontology retains the metric's source and business context so agents can recognize and use the organization's defined calculations rather than recreating them."
* [Generate an ontology from semantic models](https://learn.microsoft.com/fabric/iq/ontology/how-to-generate-from-semantic-models): the ontology gets entity types, properties, bindings, relationship types and "Metrics on entity types based on DAX measures in the semantic model".

The metrics we wrote ourselves as explicit TMDL metrics (SQL and NaturalLanguage dialect) were stored but ignored by `ask_ontology`. The TMDL reference lists explicit metrics as authorable and says nothing about agents using them. The setup that works is the documented one: entities bound to a semantic model, with the measures coming from that model.

## Limits with a semantic model binding

All three statements are in the documentation.

* Graph: [Materialize a graph from an ontology](https://learn.microsoft.com/fabric/iq/ontology/how-to-use-ontology-graph) lists "Unsupported data sources: Currently, only delta tables from lakehouses or mirrored databases are supported. Entities with data bound to a semantic model aren't supported."
* Data agent: [Create a data agent grounded in an ontology](https://learn.microsoft.com/fabric/iq/ontology/how-to-create-data-agent) says "Due to a current known issue, data agent doesn't work with an ontology that uses semantic models for binding."
* Service principal: [Consume ontology as an MCP server](https://learn.microsoft.com/fabric/iq/ontology/how-to-use-ontology-mcp-server) says "Due to a known issue, you can't currently use Service Principal to access the ontology MCP."

The Foundry page [Connect agents to Fabric IQ](https://learn.microsoft.com/azure/foundry/agents/how-to/tools/fabric-iq) names the supported authentication for an ontology: the incoming Microsoft Entra user context, or a separate OAuth session. The demo uses the first one.

Other limits from [Troubleshoot ontology](https://learn.microsoft.com/fabric/iq/ontology/resources-troubleshooting) that apply: binding does not work for semantic models in Import mode or in Direct Lake mode when the backing lakehouse is in a workspace with inbound public access disabled, and Decimal columns return null values in queries. The demo uses Direct Lake in a workspace with public access, and `double` instead of decimal.

## Binding the ontology to a semantic model by code

The bind-data page [Data binding in ontology](https://learn.microsoft.com/fabric/iq/ontology/how-to-bind-data) lists the semantic model as a supported source and requires Read and Build permission. The only documented way to produce such a binding is in the portal (**Generate Ontology** on the semantic model, or the ontology agent). The TMDL reference [Ontology definition](https://learn.microsoft.com/rest/api/fabric/articles/item-management/definitions/ontology-definition) shows the lakehouse form only. Its table partition has `mode: directLake`, `entityName`, `schemaName` and `expressionSource: DatabaseQuery`, with an `expressions.tmdl` that uses `Sql.Database`.

The demo copies what the portal writes, found by generating an ontology from the semantic model and reading it back with `getDefinition`.

| Part | Portal output, reproduced in `ontology/` |
|------|-----------------------------------------|
| `expressions.tmdl` | The portal names the expression `DirectQuery to AS - {workspaceId}/{modelId}` and opens the model with `AnalysisServices.Database("powerbi://api.powerbi.com/v1.0/myorg/WORKSPACE_NAME_PLACEHOLDER", "{modelName}")`, taking the cube `Model`. Our file uses the tested shorter name `SemanticModelSource` and concrete connection names `ProjectPlanning` and `ProjectPlanningModel` |
| Table partition | `mode: directQuery`, `entityName`, `expressionSource` set to that expression, no `schemaName` |
| Partition annotations | Six `ONT_*` source annotations per partition, copied from the portal reference and retained in live readback, see below |
| Measures | `measure 'Name' = EXTERNALMEASURE("Name", TYPE, "{expression name}")` on the tables `projects` and `tasks`, with a `///` description each |
| Entities | The generated definition has none. The demo keeps its own `ontology/entities/` files with keys and property bindings |

Notes on this path.

* `mode: directQuery` is outside the documented schema, which lists `directLake` as the only value for `mode`.
* The generated expression contains the literal text `WORKSPACE_NAME_PLACEHOLDER`. We tested a copy with the real workspace name instead. Both variants answered all duration and budget questions correctly (6 of 6 each), so the text did not affect those agent tests. The demo now stores the real name `ProjectPlanning` directly in TMDL. On 11 November the remaining environment placeholders were replaced with the existing environment's values for readability.
* We then renamed the expression to `SemanticModelSource`, together with all six partition references and four `EXTERNALMEASURE` references. The annotation-bearing live definition accepted this isolated rename and retained it in readback, with the connection body and lineage tags unchanged. After a browser reload, the Project binding preview still displayed P-100 and P-200. An ontology MCP query returned the expected budget, planned cost and variance for both projects and 31 days for T-101. The portal's long name is therefore not required in this tested definition; this is not a guarantee about every preview version or binding.
* A rule can only reference an entity that has an entity file. A rule on the generated table name `projects` failed with an unresolved reference until a full entity file with key and property bindings existed.
* A copy of the definition, uploaded with `updateDefinition`, kept the binding. A copy created with the REST create call, from the definition alone, also worked (6 of 6 answers).

All of this is derived from observation. If Microsoft changes the portal output, the files in `ontology/tables/` and `ontology/expressions.tmdl` need to follow. To check, generate a test ontology from the semantic model in the portal and compare its `getDefinition` result with these files.

## The ONT annotations

The six partitions in [ontology/tables/](../ontology/tables) now carry these annotations, shown here for [projects.tmdl](../ontology/tables/projects.tmdl):

```text
annotation ONT_WorkspaceId = 66d9c2b2-d35e-4555-9e74-926164ce584c
annotation ONT_ItemId = 22fb9927-ac28-4092-80c0-0f3d7a56a156
annotation ONT_ItemKind = SemanticModel
annotation ONT_ItemName = ProjectPlanningModel
annotation ONT_WorkspaceName = ProjectPlanning
annotation ONT_PinnedAtUtc = 2026-11-11T16:15:14+00:00
```

They are partition metadata, siblings of `source`, not fields inside the partition source. The IDs and names identify the model's workspace and the semantic model itself, not the lakehouse. The timestamp was copied from the portal-generated reference; it is not a connection address.

What they do: they name the item that a table partition reads from (workspace, item id, item kind, item name). The TMDL reference does not mention them. A report in the Microsoft repository `skills-for-fabric` ([issue 107](https://github.com/microsoft/skills-for-fabric/issues/107), open when I read it in November 2026) describes the effect for a lakehouse binding. An ontology created by REST without them could be created, but the portal showed every entity as "Ineligible" for the graph ("Unsupported Data Source / Data Source Schema Fetch Failed") and the data preview said that the kind of Fabric item could not be identified. Adding the eight annotations made the entities eligible, and the graph could be materialized. We reproduced that on 5 November: with the annotations the graph materialized, without them all five entities were ineligible.

Earlier agent tests worked without these annotations: duration and budget questions were correct (6 of 6), as were questions about emails, documents, relationships and dependencies. That did not establish portal compatibility. The main ontology's Project Instances view failed with "This data source doesn't say which Semantic model its table lives in."

On 11 November we generated a separate portal reference from the same semantic model. Its Instances view worked. We then added its six-field annotation block to each of the main ontology's six table partitions. The update preserved all existing entity, table, column and rule lineage tags, the live expression and the semantic model. All 36 annotations persisted in `getDefinition`, and the main Project Instances view displayed P-100 and P-200. An ontology MCP regression returned P-200 budget 5,000,000 EUR, planned cost 5,750,000 EUR and variance 750,000 EUR, plus 31 days for task T-101.

Only the complete six-field block was tested as the main-ontology fix. This does not prove that every field, including the timestamp, is individually required. The generated reference also had table `sourceLineageTag` values; the main fix did not add them.

## Updating a bound ontology through the API

Issue 107 also reports that sending the parts of a portal-bound ontology back unchanged with `updateDefinition` reduced the partition annotations from eight to one and made the entities ineligible. The reporter says that the annotations survive when the same parts contain all eight and the expression is the one from the reference.

Our semantic-model reference survived a `getDefinition`/`updateDefinition` roundtrip and still displayed both projects. The service normalized `ONT_ItemKind` from the generated `Dataset` to `SemanticModel`. In a separate removal experiment on that reference, five source identity annotations reappeared in readback, but the timestamp did not. This is an observation about that already-bound reference, not a guarantee that Fabric supplies missing metadata for any ontology.

Replacing the reference's identities with the main ontology's lineage tags failed with a DataSources lineage-tag error. Preserve existing identities when updating a bound ontology. For the successful main fix we changed only annotations in the six table parts and preserved every other live definition part. Before uploading the full local definition with [update_ontology.py](../scripts/update_ontology.py), compare it with the live definition and preserve any intended portal changes. Read the result back and verify both portal Instances and the semantic-model measures.

## Rule descriptions

The TMDL reference says that a `///` description and a `synonym` on a rule are accepted by the parser, "but neither is re-emitted by `getDefinition`. Do not rely on them surviving a round trip."

In our tenant the descriptions survive. `getDefinition` returned the `///` line for `EmailCriticality` and `ChangeRequestPolicy`, and `list_ontology_rules` returned a `description` field for both and the annotations of `ChangeRequestPolicy`. We did not test `synonym` on a rule. Because the reference says otherwise, treat the description as useful but not guaranteed. The rule statement is the part that the endpoint demonstrably applies: changing the statement of `EscalationPolicy` changed the answer, as the notebook shows.

## Semantic model preparation for AI

[Semantic model best practices for data agent](https://learn.microsoft.com/fabric/data-science/semantic-model-best-practices) describes how a Fabric data agent turns a question into DAX. The DAX generation tool uses the model schema, metadata such as synonyms, and the settings of Prep for AI: an AI data schema (the tables, columns and measures the AI may use), verified answers (approved example questions with a visual) and AI instructions. The page states that the tool "relies solely on the semantic model's metadata and Prep for AI configurations" and ignores instructions at the data agent level. It recommends a star schema and business-friendly names, and its example shows a model with several similar measures (Total Revenue, Gross Sales, Net Sales) where the AI picks the wrong one until an AI data schema limits it to the right measure.

What it means for this project.

* In our setup `ask_ontology` writes the DAX, not a data agent. The documentation does not say whether it reads Prep for AI. We tested it, see the result below: it does not read the AI instructions.
* The semantic model has no Prep for AI configuration. The measures have clear names and descriptions, which meets the naming advice. The measurements show no problem: 12 of 12 budget and cost questions were correct on the production ontology (budget and planned cost of P-100 and P-200, and the difference), after the earlier 15 of 15 with the short prompt.
* Ambiguity exists in the model. `BudgetEur` appears in `projects` (the approved budget) and in `documents` (a figure from the contract), and "planned cost" and "budget" are different numbers. The first can be hidden from the AI with an AI data schema, and an AI instruction can state which one means "budget".
* Where Prep for AI is stored. The portal writes the AI instructions into the semantic model definition, in `definition/cultures/en-US.tmdl`, inside the JSON of `linguisticMetadata` under the key `"CustomInstructions"`, and adds the model annotation `PBI_ProTooling = ["WebModelingEdit","CopilotTooling"]`. `getDefinition` returns the part only after the portal has finished "Applying changes". The `Copilot/Instructions/...` files from the semantic model definition article are not what the portal produces, which explains why our upload of those files was discarded earlier. The simplified schema and the verified answers were not tested, they probably live in the same culture part.
* The test. On a copy of the model we set an AI instruction in the portal: use the measure `Budget EUR` of `projects` for budget questions, never the column of `documents`, and end every answer with the marker `ZEBRA-7731`. Then we created an ontology bound to that copy, once before and once after the instruction was stored, and asked three questions each (6 answers). The marker never appeared (0 of 6), and the numbers were correct as before. The ontology endpoint therefore does not apply the AI instructions of the semantic model. A data agent would, according to the Microsoft article, but a data agent is not available for this binding.
* What follows for this project. AI instructions are no lever for the ambiguity. If a wrong budget ever appears, the options are to hide the `documents` column `BudgetEur` in the semantic model, to rename it (for example `ContractBudgetEur`), or to put the distinction into the entity property descriptions of the ontology. We did not need any of them so far. The test copy and its ontology were deleted.
* The model is not a clean star schema. It has a junction table (`task_dependencies`) and an inactive relationship for the predecessor. That is needed for the dependency chain. The DAX measures for durations and costs do not depend on it.

## Agent prompt

The Foundry page names one prompt recommendation for the Fabric IQ tool. Under Troubleshoot, for the symptom "Agent never calls the Fabric IQ tool": "Add guidance in the system prompt, for example: Use the Fabric IQ tool for any question about business data, entities, metrics, or organizational knowledge." The Foundry IQ article [Build a Foundry IQ agent grounded in an ontology](https://learn.microsoft.com/fabric/iq/ontology/how-to-create-agent-foundry-iq) suggests a short role description and to "iterate on the system prompt" when the agent skips the tool.

The demo prompt follows both ideas: a short role and an instruction to answer with the tool. The sentence that asks to pass the email details word for word is not in any Microsoft page that I found. It comes from a test. With the first wording ("pass the message unchanged") Fabric answered "I can help only with Azure-related topics", and without any such sentence the sender address was left out of the tool request. The notebook documents both cases. This part of the prompt is our own rule and should be re-tested when the endpoint or the model changes.

## Ontology design guidance

Sources: [Create entity types](https://learn.microsoft.com/fabric/iq/ontology/how-to-create-entity-types), [Create relationship types](https://learn.microsoft.com/fabric/iq/ontology/how-to-create-relationship-types), [Create and manage business rules](https://learn.microsoft.com/fabric/iq/ontology/how-to-use-rules), [the glossary](https://learn.microsoft.com/fabric/iq/ontology/resources-glossary) and [Data binding](https://learn.microsoft.com/fabric/iq/ontology/how-to-bind-data).

| Guidance | Our implementation | Result |
|----------|--------------------|--------|
| Entity types define standard names, descriptions, identifiers and properties | Five entities, each with a description, synonyms, a key and described properties (39 of 39) | Followed |
| Keys: only string and integer properties | All five keys are strings | Followed |
| Properties with the same name must have the same type across entities | Checked over all entity files, no conflicts | Followed |
| Metadata can include descriptions, synonyms and custom key-value attributes | Descriptions and synonyms everywhere, key-value attributes only on one rule | Partly, optional |
| Relationship names must be unique (known issue) | Five distinct names | Followed |
| Relationship data is in OneLake, a mapping table can carry a many-to-many relationship | The tables are lakehouse tables, `task_dependencies` carries `Task Depends On` | Followed |
| Rule definitions say what must, must not or should be true | Both rules do. `EmailCriticality` defines rating criteria, time-period comparison and the required escalation in one statement | Followed |
| Use the names and business language of the ontology in rules | The rules say "email", "project", "specification, offer or contract". The entities are `Email`, `Project`, `ProjectDocument` with the document types Specification, Offer and Contract | Partly |
| Link each entity type, property and relationship that the rule explicitly depends on | Both rules link relevant entities, properties (`propertyScope: specific`) and relationships. `EmailCriticality` includes `Project` and `ProjectManager` | Followed |
| Put rationale and supporting context in the description, not in the definition | The statements hold the rules, the descriptions hold purpose and rationale | Followed |
| Use additional metadata for category, policy, owner | `EmailCriticality` and `ChangeRequestPolicy` carry annotations such as `Category` and `Source`; the separate `EscalationPolicy` has been consolidated into `EmailCriticality` | Followed |
| Semantic models need Read and Build permission, not Import mode | Direct Lake, the deploying user owns the model | Followed |

The gaps that the audit found are closed, see the measurement below.

## Measurement: the rewrite of EmailCriticality

Setup: eight emails (the five demo emails and three synthetic ones for the boundaries), each sent four times to the Foundry agent with the question "Is it critical for our project?" and a final line `RATING: critical|watch|not critical`. The expected ratings were fixed before the runs. Sequential calls, retry after throttling.

| Version of the rule | Result | Misses |
|---------------------|--------|--------|
| Before: statement with critical and "monitored emails are watch", boundaries in the description | 30 of 32 exact | Holiday email once "watch" instead of "not critical", an office closure inside the project term once "critical" instead of "watch" |
| New statement, first wording | 29 of 32 | The delayed building permit twice "watch": the model found a schedule buffer of more than eight months and did not treat the broken offer assumption as critical |
| New statement with the clause "even when the remaining schedule buffer would absorb it" | 32 of 32 (two runs of 16) | none |

What changed in the statement: it defines all three ratings, it uses the term of the project (start and end date) to separate an absence that matters (watch) from one that does not (not critical), it places a date without a year after the date of the email, and it links the properties it depends on.

Limits of the numbers. Four runs per email are a small sample. The clause about the schedule buffer was added after the E-2 miss, so this email is no longer an independent test. The rating also depends on the model version and the data. In the synthetic case "Facade colour" the sender address is not in the data, so no project manager can be named. The rating itself was correct in all runs.

The result matches the Microsoft guidance: the statement is the part that the endpoint applies, and what a rule depends on should be linked.

## Publish before use

The Foundry page, under Admin management, says: "A Fabric admin must publish each Fabric item (ontology, data agent, or Power BI semantic model) before it can be consumed through Fabric IQ. Unpublished items aren't reachable at the MCP endpoint, and requests against them fail."

What we know.

* The demo never published the ontology or the semantic model, and the MCP endpoint answered from the first call on. The ontology item as the API returns it has `properties.generation` and no state such as draft or published.
* Only the data agent has a visible Draft and Published state with a Publish action. We saw the draft marker and the Publish entry in its menu. The data agent definition has separate `draft` and `published` folders.
* In the ontology documentation, "publish" appears in the ontology agent, whose Act mode "writes the definition to the ontology item and publishes it", and the version history captures named snapshots. Neither is a gate for consumers.

Reading: the sentence seems to describe data agents and was written for all three item types. This is my interpretation, Microsoft does not confirm it. Open question: the deploying user is the owner of the workspace and capacity administrator. A user without admin rights might meet a restriction that we cannot see. Test this with a second user before calling the setup complete.

## Tool names

The Copilot Studio page [Fabric IQ Ontology MCP](https://learn.microsoft.com/microsoft-copilot-studio/mcp-fabric-iq-ontology) lists the tools `list_ontology_entity_types` and `search_ontology`. Our endpoint lists `ask_ontology`, `list_ontology_entities` and `list_ontology_rules`. The documentation and the service differ here, and the tool names can change while the feature is in preview.

## Reference implementations

* The TMDL reference contains a complete sample, the WindFarm ontology with 35 parts, which the page says round-trips through `getDefinition` and `updateDefinition`. It uses the lakehouse form. The partition examples that I read there have no `ONT_*` annotations, and the reporter of issue 107 found no mention of them on the page. I did not read all 35 parts.
* [microsoft/fabric-clinical-supply-ontology](https://github.com/microsoft/fabric-clinical-supply-ontology) deploys an ontology with synthetic data to a lakehouse with the Fabio command line tool. It binds to the lakehouse, not to a semantic model. I read only the README.
* [microsoft/azure-ai-search-foundry-iq-live-knowledge-sources](https://github.com/microsoft/azure-ai-search-foundry-iq-live-knowledge-sources) uses the Foundry IQ path with an ontology as knowledge source. I did not read it.
* [ibranibeny/fabric-nl2gql-demo](https://github.com/ibranibeny/fabric-nl2gql-demo) is a community workshop, not a Microsoft product. The Foundry model writes GQL and an Azure Container App runs it against the graph. Its own screenshots show `graph_query_failed` for some questions.

I found no reference that builds an ontology on a semantic model by code or shows `ask_ontology` using metrics.

## Open points

* Simplified schema and verified answers of Prep for AI were not tested with the ontology endpoint. AI instructions have no effect.
* Whether a user without admin rights can use the ontology and the semantic model without a publish step.
* Repeat the rating measurement with more runs and new emails that were not used to tune the rule.
