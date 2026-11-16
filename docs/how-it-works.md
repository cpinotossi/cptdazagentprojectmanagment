---
title: How the Construction Project Planning Agent Works
description: Explains the demo for readers who know Microsoft Foundry but not Microsoft Fabric or ontologies, covering the data, the ontology, the agent connection and the deployment
---

## The scenario

A construction company runs several projects. Customers send emails all day, and a project manager has to decide for each one: does this threaten the schedule, the budget or the contract? The answer depends on facts spread over three places:

* Contract documents (specification, offer, contract) saved as PDFs
* A small project database with the project manager and key data such as budget and dates
* The incoming emails

The agent in this repository takes an email and answers three questions. Which project does it belong to? Is it critical? Who has to do what next, and by when?

Take the email "Please add a green roof with photovoltaics. This should be covered by the fixed price." The agent finds the project, notices that a green roof is outside the specification, quotes the offer clause that charges changes by effort (EUR 85 per hour), rates the email as critical and names the project manager who must be told within one working day.

## Architecture at a glance

```mermaid
flowchart LR
    U[User or notebook] -->|Responses API| A[Foundry prompt agent<br/>gpt-4.1]
    A -->|Fabric IQ tool<br/>MCP over HTTPS| O[Fabric ontology<br/>ProjectPlanning]
    O -->|bound to| S[Semantic model<br/>ProjectPlanningModel<br/>tables and DAX measures]
    S -->|Direct Lake| T[(Lakehouse tables<br/>projects, tasks, documents,<br/>emails, project_managers)]
    O -.->|rules and descriptions| R[Business rules<br/>and glossary]
```

Two platforms are involved:

* Microsoft Foundry hosts the model and the agent. It only knows one tool, Fabric IQ.
* Microsoft Fabric holds the data, the semantic model and the ontology. Fabric answers the questions the agent sends to the tool.

The agent has no search index, no function code and no copy of the data. The business knowledge sits in the ontology, the calculations in the DAX measures of the semantic model that the ontology is bound to.

## Fabric in five minutes

You do not need to learn Fabric to follow this demo. These terms are enough, each with a Foundry comparison.

| Fabric term | What it is | Foundry comparison |
|-------------|-----------|--------------------|
| Capacity | Reserved compute that every Fabric workload runs on. This demo uses the smallest paid size, F2, by default, which you can pause. Cost accrues while it runs. If you work in the portal while the agent runs, F4 is safer (`FABRIC_SKU=F4 ./deploy.sh`), see the limits at the end. | Similar to a provisioned throughput deployment: you pay for reserved capacity, not per request. |
| Workspace | A container for Fabric items, assigned to one capacity. Access is granted per workspace. | A Foundry project or resource group. |
| Item | Everything in a workspace is an item: lakehouse, notebook, report, ontology. Each item type has a REST API. | A resource inside a Foundry project, such as an agent or a connection. |
| OneLake | One storage layer for the whole tenant. Items store their data there. | Like the storage account behind a Foundry project, but shared and managed. |
| Lakehouse | An item that stores files and tables in OneLake. Tables are Delta tables. It also exposes a read-only SQL endpoint. | A database plus a file share in one item. |
| Ontology | An item that describes your business in terms of entities, relationships and rules and links them to tables. | Closest to a semantic layer or a typed knowledge base for agents. |

## What an ontology is

A table says that column `ManagerId` holds text. An ontology says that a project is led by a project manager, that an email refers to exactly one project, and that a critical email must reach the project manager within one working day.

An ontology contains five kinds of things:

| Concept | Meaning | Example in this demo |
|---------|---------|----------------------|
| Entity type | A kind of business object | `Project`, `Email` |
| Property | A fact about an entity | `Project.BudgetEur` |
| Relationship | A named link between two entity types | `Email About Project` |
| Rule | A business policy written in plain language and linked to entities | `EmailCriticality` |
| Data binding | The link from an entity or property to a table or column | `Project` is backed by table `projects` |

Descriptions and synonyms are metadata on entities, properties and relationships. A synonym such as "Project lead" for `ProjectManager` helps the agent understand a question that uses different words.

### Ontology compared with retrieval

In a typical Foundry setup an agent retrieves text chunks from an index (Azure AI Search or file search) and the model reasons over those chunks. An ontology works differently:

* The agent asks a question in natural language. Fabric turns it into structured queries over the bound tables, so numbers and joins come from the data, not from reading text.
* The meaning of the data (descriptions, synonyms, rules) is stored once in the ontology and shared by every agent that uses it. It does not have to be repeated in each prompt.
* Access is checked per user by Fabric.

> [!NOTE]
> Retrieval remains the better choice for large volumes of unstructured text. This demo keeps the PDFs small enough to store as plain text in a table column. See [Limits of the demo](#limits-of-the-demo).

## The data model of the demo

Eight tables become eight entity types. Two more tables carry many-to-many relationships without separate entity types: `task_dependencies` backs `Task Depends On`, and `supplier_materials` backs `Supplier Offers Material`.

```mermaid
flowchart LR
    E["<b>Email</b><br/>MailId (key)<br/>ProjectId<br/>Sender<br/>Subject<br/>Body<br/>ReceivedAt"]
    D["<b>ProjectDocument</b><br/>DocId (key)<br/>ProjectId<br/>DocType<br/>Title<br/>Content"]
    P["<b>Project</b><br/>ProjectId (key)<br/>Name<br/>Customer<br/>ManagerId<br/>StartDate<br/>EndDate<br/>BudgetEur"]
    M["<b>ProjectManager</b><br/>ManagerId (key)<br/>Name<br/>Email"]
    K["<b>Task</b><br/>TaskId (key)<br/>ProjectId<br/>Name<br/>StartDate<br/>EndDate<br/>CostEur"]
    S["Supplier<br/>SupplierId (key)<br/>Name"]
    B["Material<br/>MaterialId (key)<br/>Name<br/>Unit"]
    L["MaterialDelivery<br/>DeliveryId (key)<br/>SupplierId<br/>MaterialId<br/>TaskId<br/>Quantity<br/>PlannedDeliveryDate<br/>Status"]

    E -->|"Email About Project (many to one)"| P
    D -->|"Document Of Project (many to one)"| P
    K -->|"Task Of Project (many to one)"| P
    K -->|"Task Depends On (many to many)"| K
    P -->|"Project Managed By (many to one)"| M
    L -->|"Delivery From Supplier (many to one)"| S
    L -->|"Delivery Of Material (many to one)"| B
    L -->|"Delivery For Task (many to one)"| K
    S -->|"Supplier Offers Material (many to many)"| B

    classDef emailNode fill:#ffe8cc,stroke:#e8590c,stroke-width:2px,color:#5c2a00
    classDef documentNode fill:#d3f9d8,stroke:#2f9e44,stroke-width:2px,color:#0b3d1a
    classDef projectNode fill:#d0ebff,stroke:#1971c2,stroke-width:3px,color:#0b2a4a
    classDef managerNode fill:#e5dbff,stroke:#6741d9,stroke-width:2px,color:#2b1a5c
    classDef taskNode fill:#fff3bf,stroke:#e67700,stroke-width:2px,color:#5c3b00

    class E emailNode
    class D documentNode
    class P projectNode
    class M managerNode
    class K taskNode
```

Blue marks the central entity type `Project`, orange the emails, green the documents, purple the people and yellow the tasks. Arrows point from the entity that holds the foreign key to the entity it refers to. `Task Depends On` points from a task to the tasks that have to finish before it.

| Entity type | Table | Source in `data/` |
|-------------|-------|-------------------|
| `ProjectManager` | `project_managers` | `project_managers.csv` |
| `Project` | `projects` | `projects.csv` |
| `ProjectDocument` | `documents` | PDFs in `data/pdfs/`, text extracted by `setup.py` |
| `Email` | `emails` | `emails.csv` |
| `Task` | `tasks` | `tasks.csv` |
| `Supplier` | `suppliers` | `suppliers.csv` |
| `Material` | `materials` | `materials.csv` |
| `MaterialDelivery` | `material_deliveries` | `material_deliveries.csv` |
| none (join table of `Task Depends On`) | `task_dependencies` | `task_dependencies.csv` |
| none (join table of `Supplier Offers Material`) | `supplier_materials` | `supplier_materials.csv` |

The supply scenario adds two fictional suppliers, one insulation product and two
planned deliveries. Delivery DL-100 belongs to T-104 in P-100 and is due on
2027-08-02. DL-200 belongs to T-205 in P-200 and is due on 2028-01-03.
A fictional product-specific delivery stop from 2027-08-01 through 2027-09-30
conflicts only with DL-100. The delivery is linked to its project through its
existing task, not through a duplicate project key or a new milestone entity.

The assortment records link both suppliers to the same product. IsolierPartner
GmbH (S-200) is a potential replacement for BauDaemm GmbH (S-100), even without a
project delivery of its own. The rule proposes recorded alternative suppliers
but does not equate an assortment entry with confirmed availability, a price,
delivery capacity or an order. Quantity, timing, price and procurement approval
need confirmation before a switch.

The sample data has two projects, two project managers, four PDFs, five emails, 14 tasks (seven per project) and 15 dependencies between tasks. Amounts are in euros (columns `BudgetEur` and `CostEur`). The task costs of P-100 add up to exactly its budget of 2,400,000 EUR. The task costs of P-200 add up to 5,750,000 EUR, which is 15 percent above its budget of 5,000,000 EUR, so the cost warning email E-5 can be checked against the data. Every predecessor of a task ends before the task starts.

The dates and amounts use real data types (`dateTime` for dates, `double` for `BudgetEur` and `CostEur`), because sums, comparisons and date arithmetic need them. They match the column types of the lakehouse tables. All other columns are text.

### Tasks and the question about GQL

The task plan allows a question that goes over several steps: if the building permit is delayed, which tasks follow from it, directly or indirectly, what do they cost and how far does the final handover move? A first live test answered this correctly for P-100 (six dependent tasks, 2,380,000 EUR, 59 days, handover on 2028-02-12, more than the 8 weeks of the withdrawal clause) without the graph.

What the test showed:

* Fabric ran a single SQL statement that read all tasks of the project together with their predecessors (eight rows). The language model then followed the chain itself. Neither recursive SQL nor GQL was used.
* The SQL endpoint of the lakehouse cannot do recursive queries (error 8768 "Recursive CTEs are unsupported"). That would only matter if Fabric had to compute the chain in SQL.
* Without a graph the graph item in the workspace is empty. Fabric fills it only after you opt in under "Manage graph" in the portal.

For seven tasks per project the language model manages without a graph. The case for GQL begins when a project has hundreds of tasks and long chains. Then the rows no longer fit into one query result, and a traversal in the graph (`->{1,10}` in GQL) is the safer way. The experiment below shows that the endpoint that the agent uses does not run such a query.

### Graph experiment

I materialized the graph to see whether the agent then uses it.

**Eligibility needs partition annotations.** At first the "Manage graph" page showed all five entity types as "Ineligible" with the reason "Unsupported Datasource". The ontology reached Fabric through REST and TMDL, and the documented definition format does not say that the portal adds eight annotations to every partition: `ONT_WorkspaceId`, `ONT_ItemId`, `ONT_ItemKind` (value `Lakehouse`), `ONT_ItemName`, `ONT_WorkspaceName`, `ONT_SqlEndpoint`, `ONT_SqlDatabase` and `ONT_PinnedAtUtc`. Without them Fabric cannot tell which kind of item the source is, and rejects it for the graph. This is a known problem (microsoft/skills-for-fabric issue 107). Two other changes did not help: `sourceLineageTag` on tables and columns, and a OneLake expression (`AzureStorage.DataLake`) instead of the SQL endpoint. With the eight annotations written into every partition of the table files, Fabric kept them and all five entity types became eligible. That was the lakehouse binding. After the switch to the semantic model the files no longer contain them, because Fabric writes its own `ONT_*` annotations for that binding, see [microsoft-guidance.md](microsoft-guidance.md#the-ont-annotations).

**The graph itself works.** After "Materialize" in the portal the refresh job finished in about 40 seconds. The graph has one node type per entity type and one edge type per relationship (`Task_Depends_On` and four more). A direct GQL query through the Fabric REST API (`GraphModels/{id}/executeQuery`) returned the six tasks that depend on T-101 and their costs, in one traversal: `MATCH (t:Task)-[:Task_Depends_On]->{1,10}(s:Task {TaskId: 'T-101'}) RETURN DISTINCT t.TaskId, t.CostEur`.

**The agent does not use it.** With the graph in place, the agent answered the same questions correctly, but the SQL log of the lakehouse (`queryinsights.exec_requests_history`) shows that Fabric still ran SQL with a join of `task_dependencies` and `tasks` for each one. When the question asked explicitly for a graph query (GQL), `ask_ontology` only wrote the text of a Cypher query, with syntax that GQL does not accept, and executed nothing. Neither the graph nor SQL was queried for that question. So the graph does not change what the agent can do at this endpoint.

The materialized graph is a copy of the lakehouse tables. It needs a refresh after the data changes, and `./deploy.sh` does not create it. The portal does that.

**Status after the switch to the semantic model.** The graph needs an ontology that is bound to the lakehouse. Since the demo ontology is bound to the semantic model (see "A semantic model for exact numbers"), its entities can no longer be projected, and a graph item that exists in the workspace from this experiment is a stale copy. The GQL cell in the notebook works only while such a copy exists. To try the graph again, bind the tables of a test ontology to the lakehouse with the `ONT_*` annotations described above.

**A Fabric data agent with the graph as source does not work either.** The documentation names this path for natural language questions on the graph: a data agent with the graph model as a data source translates the question into GQL (NL2GQL, preview), and Foundry can reach a published data agent through the Fabric IQ tool (`/v1/mcp/workspaces/{workspace}/dataagents/{id}/agent`). I tried it twice, with an agent created through the REST API (definition parts `data_agent.json`, `stage_config.json`, and a `datasource.json` of type `graph`, which Fabric accepts) and with an agent that I created in the portal with all nodes and edges selected. The MCP endpoint of the data agent answers and lists one tool, but every question fails, even "How many tasks are there?". The run steps show that the agent passes the question to the graph and that Fabric returns `UnableToGenerateGQL`: "We couldn't translate this request into a valid graph query". Switching the agent to the preview runtime did not change that. I do not know why NL2GQL fails for this graph. In the same graph, a hand-written GQL query through the REST API works. Two points from the documentation fit: NL2GQL is a preview feature, and the query generators that the runtime page lists for the standard runtime do not include graph.

### The three business rules

The rules are plain sentences, stored in the ontology:

| Rule | Statement |
|------|-----------|
| `EmailCriticality` | Rate incoming emails critical, watch or not critical against project dates, budget and documents. Threats and document contradictions are critical even with schedule buffer. Resolve missing years; for absences and closures, state and compare the full period with project start and end. Critical emails must be escalated to the responsible project manager within 1 working day; watch and not-critical emails need no escalation. State the supporting evidence, manager name and email address, and required action. Never claim delivery without notification-tool confirmation. |
| `ChangeRequestPolicy` | A customer request outside the specification is a change request. It must be charged separately and must not be absorbed into the fixed price. |
| `SupplyDisruptionPolicy` | Match supplier and product to outstanding deliveries, compare planned delivery dates against the inclusive stop interval, and identify the task, project and manager. Distinguish supply exposure from confirmed downstream delays; an outside date is not a guarantee against future shortages. |

Each rule links relevant entities, properties and relationships to support discovery and interpretation. `EmailCriticality` combines assessment and escalation, including `ProjectManager.Name`, `ProjectManager.Email` and `Project Managed By`; the former standalone `EscalationPolicy` has been removed. Its short description explains purpose and execution boundaries, while the statement holds the requirements. The current agent has no notification tool, so it reports required escalation without sending it. See [Microsoft guidance](microsoft-guidance.md#ontology-design-guidance).

## A semantic model for exact numbers

The ontology agent calculates inexactly when the entities are bound to lakehouse tables. It counted 30 days for a task from 2027-01-01 to 2027-01-31 (31 days with both days counted), and an earlier answer called a delay of 59 days "exactly 8 weeks". Fabric does not promise otherwise: the documentation describes a natural language layer that turns questions into queries, and it lists a known issue with aggregation. Metrics are meant to come from the DAX measures of a Power BI semantic model, and the agent picks its query engine by the binding (SQL for lakehouse tables, DAX for semantic model measures).

The demo therefore binds the ontology to a semantic model, `ProjectPlanningModel`, in `semantic_model/`. It reads the ten lakehouse tables through Direct Lake, has the relationships between them and four DAX measures:

| Measure | Table | Meaning |
|---------|-------|---------|
| `Duration Days` | `tasks` | Days from the first start to the last end, both days counted. Filtered by `tasks[TaskId]` it is the duration of one task. |
| `Planned Cost EUR` | `tasks` | Sum of the task costs |
| `Budget EUR` | `projects` | Approved budget |
| `Budget Variance EUR` | `projects` | Planned cost minus budget, positive means over budget |

In [ontology/tables/](../ontology/tables) every table is declared with `mode: directQuery` and the semantic model as source. Each partition also carries six `ONT_*` annotations identifying the workspace and semantic model. Agent queries worked without them, but the portal's Project Instances view could not identify the model. Adding the annotations fixed that view without changing the model or entity identities. The two measure tables carry the measures as `EXTERNALMEASURE(...)`, a pointer to the model. This follows the source-binding form observed when you choose "Generate Ontology" on a semantic model. The entities, relationships and rules are the same files as before.

How this came out, compared with the earlier setup (ontology on the lakehouse plus the semantic model as a second Fabric IQ tool):

* **Numbers.** The agent answered 15 of 15 questions about the duration of two tasks and the budget variance of P-200 correctly, with the short prompt that has no hint about DAX, the model or its measures. The answers name the measure ("I used the model's Duration Days measure"). The second-tool setup needed a prompt with the model id, the measures, example queries and a ban on two of the Fabric tools, otherwise only one of six answers was right.
* **Rules together with numbers.** A question such as "Is P-200 over budget and what do we have to do?" returned the variance of 750,000 EUR and the action from a rule in one answer.
* **Emails and blast radius.** The five sample emails and the question for all tasks behind T-101 (six tasks, 2,380,000 EUR) came out as before. The holiday email stays unstable in both setups: it was rated "not critical" or "critical", and once assigned to the wrong project.
* **Costs.** Every question goes through DAX and the semantic model, so a capacity that is too small shows up as `CapacityLimitExceeded` when the model is opened.

The price: entities that are bound to a semantic model cannot be projected into the graph, and a Fabric data agent does not work with such an ontology yet (known issue in the documentation). The demo uses neither. The generated definition has no entity files, so the demo writes them, with keys and all property bindings, as in `ontology/entities/`.

## The ontology as files

The ontology lives in [ontology/](../ontology) as text files in TMDL, the format Fabric uses for ontology definitions through its REST API and Git integration. A shortened entity looks like this:

```text
/// A construction project for a customer.
entity Project
    backingTable: projects
    keyProperty: ProjectId
    synonym 'Construction project'

    /// Project budget in euros (EUR).
    property BudgetEur
        dataType: string
        backingConfiguration
            valueColumn: projects.BudgetEur
```

The lines starting with `///` are descriptions. `backingTable` and `valueColumn` are the data bindings. A shortened rule excerpt looks like this:

```text
rule EmailCriticality
    statement: ... Every critical email must be escalated to the project manager of its project within 1 working day. ...
    ruleReferencedEntity Email
    ruleReferencedEntity ProjectManager
```

| Path in `ontology/` | Content |
|---------------------|---------|
| `entities/*.tmdl` | One file per entity type with properties, descriptions, synonyms and bindings |
| `entityRelationships.tmdl` | The three named relationships |
| `relationships.tmdl` | The table level joins behind the relationships (foreign keys) |
| `rules/*.tmdl` | One file per business rule |
| `tables/*.tmdl` | The tables of the semantic model that the ontology reads, with their columns, and for `projects` and `tasks` the DAX measures as pointers |
| `expressions.tmdl` | The connection to `ProjectPlanningModel` in workspace `ProjectPlanning`, with concrete values stored in the file |
| `model.tmdl`, `database.tmdl`, `namespaces/default.tmdl` | Index files that Fabric requires |
| `.platform` | Item type and display name |

## How the agent reaches the ontology

The link between both platforms is a Foundry project connection and one tool.

1. A project connection of category `RemoteTool` points to the MCP endpoint of the ontology: `https://api.fabric.microsoft.com/v1/mcp/dataPlane/workspaces/{workspaceId}/items/{ontologyId}/ontologyEndpoint`.
2. The connection uses `authType: UserEntraToken`. Foundry forwards the Microsoft Entra token of the calling user, with the Power BI API as audience, so Fabric sees the real user. The user needs access to the Fabric workspace.
3. The agent is a prompt agent with one tool of type `fabric_iq_preview` that references the connection. In the SDK that is `FabricIQPreviewTool(project_connection_id=...)`.

Under the hood the tool is an MCP server. In a response you see the same item types as for any MCP tool: `mcp_list_tools` and `mcp_call`. The server offers three tools:

| MCP tool | Purpose |
|----------|---------|
| `ask_ontology` | Answers a business question in natural language, based on the ontology and its bound data |
| `list_ontology_entities` | Returns the entity types with properties, descriptions, synonyms and bindings |
| `list_ontology_rules` | Returns the business rules |

In the test runs the agent called `ask_ontology` for the email questions, and the answers contained facts from the tables, clauses from the PDFs and the rules (for example "within 1 working day"). Neither the clause nor the deadline appears in the agent prompt.

### The agent prompt

The whole prompt is [instructions.txt](../agents/fabric-iq/instructions.txt):

```text
You are a project planning assistant for construction projects.

Answer every question with the Fabric IQ tool (the project ontology). Cite the documents or clauses you rely on.
When you call the tool, write a complete question and include the details of the user's message word for word, such as sender addresses, subjects, names and dates. Do not summarize or leave out details, because the tool needs them to find the project.
Do not invent data. If the ontology does not contain the information, say so.
```

It defines the role and the tool policy. The second sentence exists because the model writes the tool request itself. Without it, the model sometimes dropped the sender address, so Fabric could not find the project. The sentence asks for a complete question on purpose: a first version that said "pass the message unchanged" made the model send only the email text, and Fabric answered "I can help only with Azure-related topics". Entity lists, field descriptions and rating criteria are deliberately absent, because they would duplicate the ontology and drift out of date.

## What happens when an email arrives

The notebook [use_case.ipynb](../notebooks/use_case.ipynb) plays this flow with a Bash kernel.

```mermaid
sequenceDiagram
    participant N as Notebook
    participant F as Foundry agent
    participant M as Fabric IQ (MCP)
    participant L as Semantic model
    N->>F: POST /openai/v1/responses with agent_reference and the email text
    F->>M: tools/call ask_ontology (question rewritten by the model)
    M->>L: DAX queries on projects, emails, documents
    L-->>M: rows
    M-->>F: answer with evidence, rules applied
    F-->>N: final answer
```

1. The notebook builds a question from sender, subject and body and sends it to the agent through the Responses API, referencing the agent by name.
2. The model decides to call `ask_ontology` and rewrites the question for the tool.
3. Fabric answers the question from the tables and rules of the ontology. In this demo the sender address matches earlier emails of one project, so the project can be identified. The answer cites clauses of that project's documents and applies the rules.
4. The agent returns the verdict, the clause it relied on and the person responsible.

## Two language models, two jobs

Two language models take part in every answer.

| Model | Runs in | Job |
|-------|---------|-----|
| `gpt-4.1` deployment (ours) | Foundry | Reads the prompt, writes the question for the tool, then turns the tool result into the final text |
| Fabric's own model (not configurable, version unknown) | Fabric | Plans the answer, writes the query (DAX against the semantic model), reads the rows, applies the rules and writes a finished answer |

What supports this:

* The tool result already is a complete answer. The field `response` of the `mcp_call` item contains the project, the verdict, the quoted rule, the clause and the next steps with deadlines, formatted as the question asked.
* The Fabric call takes 40 to 65 seconds, of which the SQL is a fraction of a second per statement. The Foundry `chat` span takes 1 to 2 seconds.
* The Fabric documentation says that Azure OpenAI models plan the work and write the queries of the ontology agent, and that the agent sends schema metadata, a few sample rows and the query results to the model. The capacity usage page lists a meter "Ontology AI Reasoning" for "the ontology MCP server and ontology agent". The first statement describes the ontology agent, the second one includes the MCP server we call, so I assume both use the same machinery. The documentation does not name the model.

The final text in the notebook is the Foundry model's rewrite of the Fabric answer. Facts, rule quotes and deadlines come from Fabric.

### Is the Foundry model needed?

For this demo, technically not. A direct call of `ask_ontology` with a complete question returned the same kind of finished answer in 41 seconds, without Foundry. The Foundry model still earns its place when you need:

* The agent as one endpoint with its own prompt, content filters, tracing and access control
* A model that extracts the email details and decides when to call the tool, including in a conversation with several turns
* More tools next to the ontology, such as a mailbox, a search index or an action that creates a ticket
* A fixed answer format and citation policy that you control, independent of Fabric's wording

It also costs something. It adds one to two seconds and tokens, and it is a source of errors seen in this demo: the dropped sender address and a rejected tool request (see Limits). If the ontology is the only source and you only need the answer text, calling the Fabric endpoint directly is the leaner option.

## Deployment

`./deploy.sh` runs two stages, because Azure Resource Manager (and with it Bicep) can create the Azure resources but not the content inside Fabric.

| Stage | Tool | Creates |
|-------|------|---------|
| 1 | [infra/main.bicep](../infra/main.bicep) | Foundry account with project management, the `gpt-4.1` deployment, the Foundry project, the Fabric capacity F2, Log Analytics and Application Insights with the Foundry connection that turns on tracing, and the Foundry role assignments for the deployer. Key based auth is disabled, access uses Entra ID only. |
| 2 | [scripts/setup.py](../scripts/setup.py), calling [scripts/setup_agent.py](../scripts/setup_agent.py) | Fabric workspace, lakehouse, tables, ontology, the Foundry connection and the agent |

Stage 2 runs these steps in order:

1. Wait until the Fabric API lists the new capacity as active. This can take a few minutes after stage 1.
2. Create the workspace on the capacity and the lakehouse in it (skipped if they exist).
3. Upload the CSV files to OneLake. Analyze each PDF with Azure Content Understanding, which returns the text and the contract terms, and upload one row per document as the source of the `documents` table.
4. Load each CSV into a lakehouse table through the Load Table API.
5. Wait until the lakehouse SQL endpoint is ready.
6. Create the semantic model from `semantic_model/` (or update its definition), with the SQL endpoint as source.
7. Read the `ontology/` folder and create the ontology (or update its definition if it exists). The TMDL files now contain static values for this demo environment, including the semantic model connection and the lakehouse SQL endpoint. Update those values before deploying to another environment.
8. Create the Foundry connection to the ontology and the prompt agent, then ask a test question.

Every step is repeatable. Running `./deploy.sh` again updates the ontology in place and publishes a new agent version.

The script signs in through the Azure CLI. Another identity, for example a service principal from environment variables, does not see the capacity or the workspace, and the connection forwards a user token anyway.

## Where to change what

| Goal | Change |
|------|--------|
| Adjust a business rule | `ontology/rules/<Name>.tmdl` |
| Add a description or synonym | `///` lines and `synonym` lines in `ontology/entities/` |
| Add a table | A CSV in `data/`, `ontology/tables/<table>.tmdl`, a `ref table` line in `ontology/model.tmdl`, plus an entity file |
| Change the role or tool policy of the agent | `agents/fabric-iq/instructions.txt` |
| Change the model or the capacity size | Parameters in `infra/main.bicep` |

Run `./deploy.sh` after any change.

## Observing a call

The last section of the notebook [use_case.ipynb](../notebooks/use_case.ipynb) follows one call through both platforms. The table shows where each source of evidence comes from.

| Question | Source | Where to look |
|----------|--------|---------------|
| Which agent configuration ran? | The Responses API echoes agent version, model, prompt and tool | Response JSON |
| What did the model ask Fabric? | `mcp_call` item and the span `execute_tool` | Response JSON, trace |
| How long did each stage take, how many tokens? | Server-side trace in Application Insights | Table `AppDependencies`, filter on `gen_ai.response.id` |
| Which configuration does Fabric hold? | MCP tools `list_ontology_rules` and `list_ontology_entities` | Direct call with your token |
| Which DAX queries did the endpoint run against the semantic model? | Workspace monitoring, table `SemanticModelLogs` of the monitoring Eventhouse | KQL. Switch on monitoring in the workspace settings first. The `OperationId` of a DAX query is the `client_correlation_id` of its SQL statements |
| Which statements reached the lakehouse, and as whom? | `queryinsights.exec_requests_history` of the lakehouse SQL endpoint | T-SQL, for example with `sqlcmd`. The ontology is bound to the semantic model and asks it with DAX. The statements in this log are what the semantic model reads from the lakehouse, and many carry a label with the `DatasetId` of the model. |

A typical call has three spans. `invoke_agent` is the whole call, `execute_tool` is the Fabric call and `chat` is the model writing the final answer. In the measured runs, 95 percent or more of the time sat in the Fabric call, and the SQL statements themselves took well under a second each. The remaining time is spent inside Fabric before and between the statements.

Fabric has no separate log for ontology calls as such. Workspace monitoring logs the DAX queries that the endpoint writes against the semantic model, and Query Insights logs the SQL that reaches the lakehouse. The notebook [request_walkthrough.ipynb](../notebooks/request_walkthrough.ipynb) puts all layers of one request side by side. The SQL statements in Query Insights are the closest view. They show the user as `login_name`, which confirms that the Entra token passes through, and they show joins that follow the bindings and relationships of the ontology.

Traces reach Application Insights a few minutes after the call, so the notebook waits for them.

A live test confirmed that the ontology configuration drives the answer, and the notebook repeats it for the rule `ChangeRequestPolicy`. After changing the rule `EscalationPolicy` to "by phone within 2 hours" and uploading the ontology, the agent repeated that wording with the same prompt and data. In the notebook, replacing the statement of `ChangeRequestPolicy` by "quoted with a 15 percent surcharge and approved in writing" made the answer quote the surcharge and the written approval. The original rule is restored afterwards, in the notebook through an exit handler that runs `scripts/update_ontology.py`.

## Limits of the demo

* Fabric IQ and the ontology API are in preview. Names and behavior can change.
* PDF text is stored as one text column per document. There is no chunking, no embedding and no semantic search. This works for one page per document. For large document sets, a search index (for example Azure AI Search) fits better.
* Until the data types were corrected, the entities `Project` and `Email` declared their dates and the budget as text, although the lakehouse stores them with proper types. That worked in all tests, but the types are now aligned with the tables.
* Answers come from a language model, so wording and borderline ratings vary between runs. The same holiday email (a customer's office is closed over the holidays) was rated "not critical" in six of nine valid runs and "watch" in three. Both fit the rule.
* Moving the rating boundaries from the description into the statement of `EmailCriticality` made the ratings more stable, see [the measurement](microsoft-guidance.md#measurement-the-rewrite-of-emailcriticality). An earlier long description with examples had not (holiday email: "not critical" five times and "watch" five times in ten runs). The statement is the part that the endpoint applies.
* The model sometimes calls `list_ontology_rules` with an empty filter list. The server rejects that call and the request fails with HTTP 400. The prompt therefore does not ask the agent to list rules.
* The model writes the tool request itself and can still drop details such as the sender address. The prompt tells it to include them word for word, which worked in five of five test runs, but a language model gives no guarantee. Fabric also gives different ratings for the same email. In three runs the site plan email was rated "not critical" twice and "critical" once.
* Fabric limits the rate of requests to the ontology endpoint. Five agent calls at the same time returned HTTP 429 "RequestBlocked" for 8 of 15 calls, and the agent showed the error text as its answer. Even one call after the other was refused often during a longer series of about 30 calls, each time with a block of about a minute. A larger capacity does not remove this: I scaled the capacity from F2 to F4 for a test and repeated the same five parallel calls three times. 8 of 15 calls worked, against 7 of 15 on the F2. A simple question still took about 50 seconds when it worked. NL2GQL in a data agent failed the same way on F4, and the Fabric portal did not load faster. The rate limit therefore looks like a limit of the endpoint, not of the capacity. The F2 is still small for this setup, though. After a day of tests (ontology, semantic model, graph, data agents, many agent calls), the portal refused to open the semantic model on the F2 with "Unable to load model due to reaching capacity limits" (`CapacityLimitExceeded`). The documentation names F2 as the minimum for the ontology. Plan for an F4 if you work in the portal while the agent runs, and pause or scale down when you are done.
* Now and then the endpoint accepts a call but returns no valid answer ("did not return a valid MCP JSON-RPC response"). A repeated call works.
* Calculations by the model are not exact. An earlier answer called the delay from 2027-01-31 to 2027-03-31 "exactly 8 weeks", but it is 59 days.
* Metrics did not help. I added two explicit metrics through TMDL, `PlannedCostEur` (sum of the task costs) and `TaskDurationDays` (days from start to end, both days counted), once with the SQL dialect and once with the NaturalLanguage dialect. Fabric stored them, but `ask_ontology` ignored them in both variants, even when the question used the exact metric name. It answered 30 instead of 31 days for task T-101 and gave the project budget instead of the task sum. The documentation explains why: metrics are meant to come from the DAX measures of a Power BI semantic model, and the agent chooses its query engine by the binding (SQL for lakehouse tables, DAX for semantic model measures). The documentation does not say that the agent uses explicit metrics on lakehouse entities. The explicit metrics from TMDL did not change that. What works is the documented path: bind the ontology to a semantic model with DAX measures, see "A semantic model for exact numbers".
* The tenant setting "Users can create Ontology (preview) items" must be on, and the Fabric capacity must be running. Pause the capacity when you are done to stop the cost.

## Glossary

| Term | Meaning |
|------|---------|
| Delta table | Open table format that lakehouse tables use |
| MCP | Model Context Protocol, the standard Foundry uses for remote tools |
| OneLake | The single storage layer of a Fabric tenant |
| SQL endpoint | Read-only T-SQL access to the tables of a lakehouse |
| TMDL | Text format for model definitions, used here for the ontology files |

## Further reading

* [Ontology overview](https://learn.microsoft.com/fabric/iq/ontology/overview)
* [Agent integration for ontology](https://learn.microsoft.com/fabric/iq/ontology/concepts-agent-integration)
* [Connect agents to Fabric with Fabric IQ](https://learn.microsoft.com/azure/foundry/agents/how-to/tools/fabric-iq)
* [Business rules in ontology](https://learn.microsoft.com/fabric/iq/ontology/how-to-use-rules)
* [Ontology definition format](https://learn.microsoft.com/rest/api/fabric/articles/item-management/definitions/ontology-definition)
* [Alignment with Microsoft guidance](microsoft-guidance.md): which steps follow the documentation, which go beyond it, and what stays open
