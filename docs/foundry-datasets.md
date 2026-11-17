---
title: Canonical business-test dataset
description: One six-case reference shared by Foundry Portal, notebooks and GitHub Actions
---

## Source and generated files

Edit only [business_tests.json](../evaluations/business_tests.json) for the six
business cases B-001 through B-006. The
[dataset catalog](../evaluations/datasets.json) generates one canonical reference
export. Commit the source and generated JSONL together; never edit the export
independently. The portal, notebooks and CLI publish the same bytes under
`project-planning-business-reference-cases`.

| Kind | JSON source | Generated JSONL | Purpose |
|------|-------------|-----------------|---------|
| `cases` | [Business cases](../evaluations/business_tests.json) | [Canonical reference](../evaluations/generated/business_reference.jsonl) | All six cases; full criteria and critical errors serialized as JSON strings for portal evaluator compatibility |
| `calibration` | [Policy-scope controls](../evaluations/business_judge_policy_scope_controls.json) plus the matching business case | [Local policy-scope calibration](../evaluations/generated/policy_scope_calibration.jsonl) | Local-only synthetic answers joined to the current reference for evaluator testing; never published as a Foundry dataset |

The only published dataset is `project-planning-business-reference-cases`,
with immutable versions. Direct business evaluations pass its `file_id` to
Foundry with an `azure_ai_target_completions` agent target. Foundry generates the
answers and judges them; the verified six-case direct run created no new dataset.
There is no inline payload or registered per-agent answer dataset.
Expected answers, context and criteria are for evaluation;
send only `query` to the agent. The catalog does not copy business-rule execution
into the release pipeline. Ontology rules remain authoritative.

Cloud judging of saved captures, recorded-run replays and synthetic calibration
controls is disabled. Their CLI options fail before cloud access, and invoking
the runner without an explicit mode no longer starts calibration.
The historical rubric replay is disabled too: its derived `rubric_query` packet
is not part of the canonical dataset. Controls and replay helpers remain local.

## Local judge calibration

[judge_calibration.py](../scripts/judge_calibration.py) tests the evaluator prompt
against fixed answers without creating any Foundry dataset, evaluation or run. It
renders the prompt from the canonical reference, calls the judge deployment
directly and compares the verdict with the expected label. Expected labels are
never sent to the judge.

The fixture [judge_calibration.json](../evaluations/judge_calibration.json) holds
twelve real answers (six cases, Fabric IQ and Basic) and exact text-edit
variants of them: missing points, invented claims and paraphrases. A variant may
build on an earlier variant to isolate one defect. Its labels are marked
`proposed` until a human confirms them. Borderline items are reported but
excluded from the agreement count.

The evaluator prompt asks for one structured line per criterion and critical
error (`C | id | MET | "verbatim quote"`). The runner then decides
deterministically, and compares the judge's own boolean only as a cross-check:

- every ID must be present; missing, unknown or malformed lines make the run
  unusable instead of a PASS or FAIL;
- a `MET` needs a quote that occurs verbatim in the answer (ellipsis-separated
  segments must occur in order) and satisfies every `anchors` entry of the criterion;
- a criterion with `"absence": true` is a prohibition and needs the marker `ABSENT`;
- one `NOT_MET` or one `PRESENT` critical error fails the item. `U` lines, the
  judge's free-form "unsupported claim" findings, are counted and shown but never
  change the verdict: they proved unstable between runs, and named critical errors
  already cover the known invention types. A claim outside those types is therefore
  not caught automatically.

A quote that exists in the answer can still fail to support the criterion. Only
`anchors` (IDs, e-mail addresses, numbers) catch that deterministically.
Semantic criteria such as a required clarification point remain a judgment of
the model, so the runner reports them against the expected failures per
criterion.

An anchor is a string the answer must contain, or a list of alternative
spellings. The same definition feeds the Foundry code-based evaluator
[business_anchors.py](../evaluations/evaluators/business_anchors.py), which
scores the share of checks that hold in the generated answer without any LLM.
It cannot see criteria without anchors and matches substrings, so `500.000` also
matches `5.500.000`. Two further fields refine prohibitions:

- `violation_anchors` on a critical error or a prohibition: a reported violation
  counts only if its quote contains one of them. This discards a judge finding that
  merely sounds related, for example "muss zwingend bestätigt werden" as a deadline.
- `forbidden`: regular expressions that fail the criterion whenever they match the
  answer, for example `planmäßig` for a hedged delivery promise. Use them only for
  phrases that never occur in a correct answer; negations such as "kann nicht
  geliefert werden" make most candidates unusable.

**Do not use the line check as a verdict.** Applied to ten real Fabric IQ runs it
rejected far more items than the Foundry judge (for B-003 six of ten) because the judge
often combines table cells, adds its own remarks in parentheses or changes quotation
marks, so the quote is not verbatim although the substance is right. Used on
calibration items it agreed 31 of 32 times, so the figure does not transfer to
real runs. Read it as a diagnostic that points to answers worth reading.

```bash
.venv/bin/python scripts/judge_calibration.py --repeats 3
```

The local file [business_correctness.json](../evaluations/evaluators/business_correctness.json)
holds the structured prompt registered as evaluator v14. Foundry itself only
reads the judge's boolean `result`; the per-line checks above run in the local
calibration tool, which is a development aid for prompt changes. In operation
the deterministic part in Foundry is the anchor evaluator, which gave the same
scores in the sandbox as a local recomputation on all twelve pilot answers.

Foundry reports only a number for a code-based evaluator, not which anchors
are missing. Recompute them locally with `business_anchors.check_anchors`.

The direct model call is not the managed Foundry evaluation path. It tests prompt
and model behavior; the managed agent run remains the end-to-end check. Results go to
the ignored `evaluations/runs/judge_calibration/` folder.

Inline `file_content` means transmitting input rows in the evaluation request,
not avoiding storage. A live test showed that Foundry persists these rows as an
`eval-data-*` dataset. The previous inline migration was replaced with the
existing-dataset agent path. Publication and evaluation never delete assets
automatically; any old test snapshots require explicit, exact-ID cleanup.

The older [email controls](../evaluations/business_judge_controls.json) use a
pre-consolidation policy and are deliberately not included in this active
catalog. They remain historical regression inputs with their archived reference.
Adding a current email calibration set requires reviewed answers, not a mechanical
rule-name replacement.

## Generate, check and publish

```bash
.venv/bin/python scripts/foundry_datasets.py generate
.venv/bin/python scripts/foundry_datasets.py check
```

`generate` performs no cloud operations. `check` fails when any committed JSONL is
missing or differs byte-for-byte from its JSON sources. Rows are sorted by ID and
object keys are sorted; UTF-8 output and newline handling are deterministic.
The manual exporter uses the same deterministic serializer.
`manual_agent_test.py export` writes the canonical export, not another projection.

For a signed-in developer, the same upload command works in a terminal or Bash
notebook:

```bash
.venv/bin/python scripts/foundry_datasets.py publish \
  --endpoint 'https://ai-projplan-axowf3.services.ai.azure.com/api/projects/project-planning' \
  --confirm \
  --manifest evaluations/notebook_runs/dataset-publication/release.json
```

All catalog exports, including local calibration inputs, must be current before
publication. Only `cases` entries are published; calibration-only publication
fails explicitly. Publication uploads the exact checked reference
JSONL, reuses the shared registration helper, and reads back the resulting asset
IDs. It retains stable names and versions files by the first 16 characters of
their SHA-256. It never overwrites an existing version or deletes old versions.
An identical file is reused rather than uploaded again.

The local manifest records names, IDs, versions, hashes, row counts, endpoint
and, in Actions, the commit SHA. It is saved after each upload. An interrupted
release is marked `publishing`, not `published`; rerunning reuses completed
versions.

## Notebook compatibility and outputs

Open [publish_datasets.ipynb](../notebooks/publish_datasets.ipynb) with the Bash
kernel. It generates and checks every catalog export. Upload is off by default;
set `PUBLISH_DATASETS=yes` explicitly to authorize the upload cell.

Business notebooks use `--publish-reference` to check and publish the same
canonical JSONL as the GitHub Action. Their manifests pin its ID, version and
SHA-256. `--reference-asset` downloads those exact bytes into
`reference.asset.data.jsonl`. Direct evaluations reuse that canonical dataset ID
and map `response` to `{{sample.output_text}}`, not a saved answer.
The reference hash always identifies the downloaded file, not a reserialized copy.

Existing saved notebook outputs remain historical evidence until rerun. Captures
from the old JSON reference have a different file hash and cannot be mixed with
the new JSONL reference. For a new comparison, rerun the direct evaluation cells
in [business_all_tests.ipynb](../notebooks/business_all_tests.ipynb).
It invokes both pinned agents and produces two six-case reports without local
capture or judge-only stages. Retained old manifests can still read JSON
snapshots if the remote asset exists, but new direct runs require canonical JSONL.

Each local evaluation report stores the pinned reference ID, version, SHA-256,
agent and evaluator versions, generated answers under `output_items[].sample`,
and Foundry judgments. `--resume` retrieves the same run without new inference.
A fresh agent-evaluation command generates new answers, not a replay of old ones.

The B-001, B-002 and B-005 notebooks retain their direct response and trace
diagnostics. Their saved-answer judge and comparison cells were removed.
They neither register evaluator versions nor start evaluations.
Their captures are separate agent calls, not the answers judged in the suite.
The managed evaluation path does not apply the local capture handoff gate;
inspect diagnostic traces when investigating routing or tool failures.

### Foundry Portal

Select `project-planning-business-reference-cases` with the intended pinned
version and use `{{item.query}}` as the user prompt. Never send context, expected
answers or criteria to the agent. For the business-correctness evaluator, map
`query`, `context`, `ground_truth`, `criteria` and `critical_errors` to their
`{{item.FIELD}}` values and `response` to `{{sample.output_text}}`.

The legacy `project-planning-business-reference` JSON asset,
`project-planning-business-checks` projection and the pre-migration version of
`project-planning-business-reference-cases` are superseded. Their Azure versions
are removed during the one-time cleanup; historical reports remain, but runs
referencing deleted assets cannot be replayed directly. Publication never deletes
assets automatically. Old local archived references and frozen test fixtures
remain historical inputs, not active sources.
The subsequent single-dataset cleanup also removes all versions of the five
`project-planning-business-inputs-*` assets (agent, baseline, Logic App,
comparison and calibration). Historical reports remain, but replaying their old
`file_id` requests is no longer possible. New direct runs use the canonical
reference's existing `file_id`, not inline payloads.

Notebook `.ipynb` files, including execution counts and outputs, remain in Git.
There is no output-stripping hook or CI check that rejects notebook outputs.
Review the displayed outputs for sensitive information before committing.

Separate captures, traces, evaluator reports, reviews, baseline snapshots and
release manifests stay local in the ignored `evaluations/notebook_runs/`,
`manual_runs/`, `runs/`, `baselines/` and `releases/` directories and the equivalent
archive output directories. Existing run reports were removed from the Git
index only; their local files and previous Git history remain intact.
Curated replay inputs in [tests/fixtures/business/](../tests/fixtures/business/)
retain only the reference fields and frozen assistant messages required by
offline regression tests, not live evaluation reports, scores or traces.

## GitHub Actions

The [Foundry datasets workflow](../.github/workflows/foundry-datasets.yml) checks
JSON/JSONL consistency and focused tests on relevant pull requests and main pushes.
It never regenerates files silently in CI and uses no JSONL build artifacts.

Manual dispatch with `operation=publish` publishes only the committed canonical
reference JSONL from main. Calibration JSONL is checked locally but not uploaded.
The publish job repeats the freshness check, uses OIDC and serializes releases.
The release manifest is displayed in the run summary, not committed to Git.
Automatic pushes validate only; they do not upload datasets or run paid inference.

### Administrative identity bootstrap

The [Foundry identity template](../infra/foundry-release-identity.bicep) creates
UAMI `id-projplan-github-foundry`, one federated credential and one Foundry User
assignment scoped to the existing `project-planning` project. It does not modify
the Foundry account, project, agents, datasets or the Logic Apps identity.
The approved bootstrap was deployed and independently read back on 2026-11-14.
Repeat only as an administrator with role-assignment permissions:

```bash
az deployment group what-if \
  --subscription 98525264-1eb4-493f-983d-16a330caa7f6 \
  --resource-group rg-projplan-demo \
  --name github-foundry-oidc-bootstrap \
  --template-file infra/foundry-release-identity.bicep --mode Incremental

az deployment group create \
  --subscription 98525264-1eb4-493f-983d-16a330caa7f6 \
  --resource-group rg-projplan-demo \
  --name github-foundry-oidc-bootstrap \
  --template-file infra/foundry-release-identity.bicep --mode Incremental
```

The credential trusts the exact subject
`repo:cpinotossi@8876980/cptdazagentprojectmanagment@1442137267:environment:foundry-demo`,
issuer `https://token.actions.githubusercontent.com` and audience
`api://AzureADTokenExchange`. It is environment-scoped, not workflow-scoped.
No client secret is needed or created.

Foundry User is a built-in project-development role with broader data-plane
permissions than dataset upload alone; it is not a custom dataset-only role.
No Contributor, Owner, storage role or role-assignment permission was assigned
to this identity. The separate Logic Apps identity remains RG Contributor and
can modify identities in the same resource group, including this federation.
Separate identities in this shared resource group are therefore not a complete
administrative isolation boundary.

### GitHub environment and first publication

Environment `foundry-demo`, its main-only branch deployment policy and the three
variables below have been created and read back. These IDs are not secrets:

| Variable                | Value                                  |
|-------------------------|----------------------------------------|
| `AZURE_CLIENT_ID`       | `0ba22d37-90be-41ea-ab63-0b5ce61004ad` |
| `AZURE_TENANT_ID`       | `f71980b2-590a-4de9-90d5-6fbc867da951` |
| `AZURE_SUBSCRIPTION_ID` | `98525264-1eb4-493f-983d-16a330caa7f6` |

No required reviewer is configured. The existing private-repository plan has
previously rejected ruleset access with an upgrade requirement, and main remains
unprotected. Restricting deployments to main and requiring manual dispatch do
not provide independent approval. Protect workflow changes and configure
reviewers where supported before extending this demo to production.

Azure deployment success and configuration readback do not prove GitHub-issued
OIDC access or dataset upload. The workflow was published in commit `c498f01`
and both the [check run](https://github.com/cpinotossi/cptdazagentprojectmanagment/actions/runs/43587714271)
and the [OIDC publication run](https://github.com/cpinotossi/cptdazagentprojectmanagment/actions/runs/43587746018)
succeeded. Those historical runs predate the consolidation. The workflow now
publishes only the canonical reference. For subsequent releases:

1. Dispatch `Foundry datasets` on main with `operation=check`.
2. Dispatch it again with `operation=publish`.
3. Check Azure Login, the dataset upload/readback steps and the run summary.
   The summary must record exactly one dataset name, version and hash, as well as
   the selected commit SHA.

The workflow does not bootstrap its own identity or grant itself roles.

This workflow publishes datasets only. Agent/evaluator releases, live evaluations,
Fabric authentication and production promotion are separate concerns. In
particular, successful dataset upload does not prove delegated Fabric IQ access.
