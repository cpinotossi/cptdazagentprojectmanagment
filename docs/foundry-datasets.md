---
title: Versioned JSON and JSONL datasets
description: Shared dataset generation and publication from notebooks and GitHub Actions while keeping notebook outputs in Git
---

## Source and generated files

Edit JSON sources and commit both those sources and their generated JSONL files.
The [dataset catalog](../evaluations/datasets.json) declares each Foundry name,
kind, source files and generated output. Multiple datasets and multiple JSON
sources per dataset are supported; IDs must be unique within each dataset.

| Kind | JSON source | Generated JSONL | Purpose |
|------|-------------|-----------------|---------|
| `checks` | [Business cases](../evaluations/business_tests.json) | [Business checks](../evaluations/business_tests.jsonl) | Question/context/expected-answer projection for agent-target evaluations |
| `cases` | Business cases | [Full reference cases](../evaluations/generated/business_reference.jsonl) | Complete case records, including criteria, critical errors and source declarations |
| `calibration` | [Policy-scope controls](../evaluations/business_judge_policy_scope_controls.json) plus the matching business case | [Policy-scope calibration](../evaluations/generated/policy_scope_calibration.jsonl) | Synthetic answers joined to the current reference for evaluator testing |

Calibration `expected_pass` labels remain in the JSON controls, not in the
uploaded judge input. Expected answers, context and criteria are for evaluation;
send only `query` to the agent. The catalog does not copy business-rule execution
into the release pipeline. Ontology rules remain authoritative.

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
The existing manual exporter and runtime answer uploads use the same serializer.

For a signed-in developer, the same upload command works in a terminal or Bash
notebook:

```bash
.venv/bin/python scripts/foundry_datasets.py publish \
  --endpoint 'https://ai-projplan-axowf3.services.ai.azure.com/api/projects/project-planning' \
  --confirm \
  --manifest evaluations/notebook_runs/dataset-publication/release.json
```

Use repeatable `--dataset NAME` options to publish a subset. All catalog exports
must still be current before publication. Publication uploads the exact checked
JSONL, reuses the shared registration helper, and reads back the resulting asset
IDs. It retains stable names and versions files by the first 16 characters of
their SHA-256. It never overwrites an existing version or deletes old versions.
An identical file is reused rather than uploaded again.

The local manifest records names, IDs, versions, hashes, row counts, endpoint
and, in Actions, the commit SHA. It is saved after each upload. An interrupted
release is marked `publishing`, not `published`; rerunning reuses completed
versions. Dataset publication is not an atomic multi-asset transaction.

## Notebook compatibility and outputs

Open [publish_datasets.ipynb](../notebooks/publish_datasets.ipynb) with the Bash
kernel. It generates and checks every catalog export. Upload is off by default;
set `PUBLISH_DATASETS=yes` explicitly to authorize the upload cell.

Existing business notebooks and their saved outputs are not rewritten. Their
`--publish-reference` path still stores an exact JSON reference snapshot, and
their existing manifests continue to download and hash-check those bytes.
The new full-case JSONL is a separate asset, not a replacement for that snapshot
or a drop-in replacement for a reference manifest. Runtime agent-answer datasets
still originate from actual captures; the catalog does not fabricate answers.

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

Manual dispatch with `operation=publish` publishes the committed JSONL from main.
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
OIDC access or dataset upload. The new workflow is still local at this bootstrap
stage. After reviewing, committing and publishing it through the normal Git
process, verify the release:

1. Dispatch `Foundry datasets` on main with `operation=check`.
2. Dispatch it again with `operation=publish`.
3. Check Azure Login, the dataset upload/readback steps and the run summary.
   The summary must record all three catalog names, versions and hashes, as well
   as the selected commit SHA.

The workflow does not bootstrap its own identity or grant itself roles.

This workflow publishes datasets only. Agent/evaluator releases, live evaluations,
Fabric authentication and production promotion are separate concerns. In
particular, successful dataset upload does not prove delegated Fabric IQ access.
