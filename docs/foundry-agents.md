---
title: Existing Foundry agent releases
description: Offline checks and explicit immutable releases of the Fabric IQ and static baseline agents using project-scoped OIDC
---

## Release scope and sources

[Foundry agents](../.github/workflows/foundry-agents.yml) releases exactly
`project-planning-agent` and `project-planning-baseline`. It does not create
infrastructure, connections, models, identities, role assignments, datasets or
evaluators. It never runs inference or accesses Fabric.

Agent configuration is built from **committed sources**, not copied from an old
cloud reference:

* [Fabric IQ config](../agents/fabric-iq/config.json) explicitly declares the
  existing environment's endpoint, model deployment and connection ARM ID.
  Changing the model or environment binding requires a reviewed config commit.
  The endpoint and connection must identify the same account/project.
* `agent_definition` in [setup_agent.py](../scripts/setup_agent.py) is shared by
  the original setup helper and this release. It constructs the Fabric IQ tool,
  approval behavior, exact committed
  [instructions](../agents/fabric-iq/instructions.txt), and `AGENT_SETTINGS`.
  Runtime settings currently remain unspecified, matching original setup;
  supported settings can be explicitly committed in `AGENT_SETTINGS`.
  Tool changes belong in this shared builder, not in a cloud-only reference.
  The full original-message handoff to `ask_ontology` remains intact. No synonym
  translation is added; `qwert` resolution remains the ontology's responsibility.
* `baseline_definition` in
  [setup_baseline_agent.py](../scripts/setup_baseline_agent.py) combines that
  newly built definition's supported model/runtime settings with frozen evidence
  and **no tools**. The [Basic config](../agents/basic/config.json) declares its
  name and reference agent; [policy.txt](../agents/basic/policy.txt) holds its
  evidence policy. The reference must match the Fabric IQ agent in this paired
  release. It never copies Fabric IQ instructions into the baseline.

Shared setup, connection and release logic remains under
[scripts/](../scripts/); component folders contain editable sources, not copied
deployment code. The snapshot marker and serialization remain in the shared
Basic builder. No captured records, credentials or run outputs were moved into
the component folders.

Every publish requires one **explicit published numeric version**:
`baseline_source_version`, an existing `project-planning-baseline` version
containing the approved data-only static snapshot. Its instructions must have
the explicit `Static reference snapshot (JSON):` marker emitted by the baseline
helper. Its old policy/model/settings are deliberately ignored; the current
committed policy applies to the unchanged evidence. No Fabric IQ source version
is read or required.

This is the reproducible baseline source, not a checked-in live capture.
Ignored `evaluations/baselines/` files are neither required nor uploaded.
No automatic `latest`, live refresh, CSV/PDF reconstruction, fabricated records
or empty fallback exists. The original capture timestamps, source IDs, note and
all accepted table records remain intact. Existing six-table snapshots and the
helper's complete supply/portfolio extensions are supported; partial table sets,
missing capture provenance, unknown metadata and empty project evidence fail
before either version is created. Empty individual tables can be legitimate.

Keep the selected source versions available in Foundry. Inspect their provenance
and data freshness before choosing them: a hash proves identity, not accuracy or
freshness. CI checks cannot prove the authenticity of historical capture metadata.
To refresh the baseline, a separately authorized operator must capture it using
delegated user access via the existing setup helper and review it first. **Do not
run that helper's live-capture path under the release service principal.**
An incompatible snapshot format requires an explicit reviewed migration; the
release uses the stable marker, not guessed JSON boundaries. Historical policy
instructions are not evidence and never enter the released baseline policy.

## Check and publish

Checks on relevant pull requests, main pushes and default manual dispatch are
offline, with only `contents: read` and no Azure login:

```bash
.venv/bin/python scripts/foundry_agents.py check
.venv/bin/python -m unittest discover -s tests -p 'test_foundry_agents.py' -v
.venv/bin/python -m unittest discover -s tests -p 'test_setup_agent.py' -v
.venv/bin/python -m unittest discover -s tests -p test_setup_baseline_agent.py
```

`check` validates committed instruction availability; focused tests exercise the
release plan, source rejection, retries and readback with synthetic test records.
It does **not** claim to validate remote source versions or Fabric connectivity.
No cloud privileges are available to the validation job.

After review and normal merge, manually dispatch **Foundry agents** on **main**,
choose `operation=publish`, and enter the approved baseline evidence version.
Blank source inputs are allowed only for `check`; publication rejects them
before Azure login. There are no built-in source-version defaults.

The publish job uses existing environment `foundry-demo`, its main-only
deployment policy, and its dedicated project-scoped Foundry User OIDC identity.
Only that job has `id-token: write`. Required existing environment variables:

| Variable | Expected value |
| -------- | -------------- |
| `AZURE_CLIENT_ID` | `0ba22d37-90be-41ea-ab63-0b5ce61004ad` |
| `AZURE_TENANT_ID` | `f71980b2-590a-4de9-90d5-6fbc867da951` |
| `AZURE_SUBSCRIPTION_ID` | `98525264-1eb4-493f-983d-16a330caa7f6` |

The endpoint is explicitly committed in `agents/fabric-iq/config.json` for the
existing `project-planning` project. No ARM Contributor or Fabric principal grant
is needed or added. The existing ontology connection must already be configured
for delegated `UserEntraToken` access by infrastructure/bootstrap operations.
The original
`create_connection` helper performs an ARM PUT with `authType=UserEntraToken`;
publishing an agent definition merely references that existing connection ID.
The release calls neither `create_connection` nor `setup_fabric`. Consequently,
delegated Fabric authentication does not block definition publication, but
creating/changing the actual connection or ontology is outside this workflow.
Successful publication does not verify end-user Fabric authorization.
The environment currently has no required reviewer; main-only dispatch is not
independent approval. Protect workflow changes and add reviewers where supported.

For an already signed-in authorized developer, the same helper is available
locally (replace the placeholder with a reviewed numeric version):

```bash
.venv/bin/python scripts/foundry_agents.py publish --confirm \
  --baseline-source-version '<baseline-version>'
```

`--config` can explicitly select another reviewed configuration file locally.
The workflow always uses the committed default; endpoint/model/connection have
no environment-variable or cloud-reference fallback. The original setup script
still receives its deployment name from infrastructure outputs; coordinate that
name with the release config when infrastructure changes. Publication does not
create model deployments. Unspecified runtime settings retain service defaults;
pin them explicitly where reproducibility across service changes is required.

## Versioning, evidence and recovery

Agent names remain stable. Foundry allocates a new published immutable version;
the helper never updates or deletes existing versions. A canonical full-definition
SHA-256 in release metadata allows reruns to reuse an identical published version.
Every created or reused version must match the complete requested definition,
name and numeric version on explicit `get_version` readback. A matching metadata
hash alone is insufficient. The evidence lookup never selects a mutable latest.

The ignored `evaluations/releases/agents.json` manifest records the commit,
baseline source version/hash, explicit configuration/hash, resulting definition
hashes and versions, reuse flags and pair status. It intentionally contains
**no** instructions or snapshot records.
Actions writes it to the run summary even on partial publication failure.
The two agents are not an atomic transaction: if the second fails, the manifest
remains `publishing`; rerun with the same evidence version and commit to reuse the
first and complete the second. Workflow publication is serialized; external
publishers must coordinate separately to avoid duplicate equivalent versions.
If creation succeeds but the connection fails before its response arrives, rerun
and let metadata lookup/readback recover the published version.

Existing notebook invocation, saved outputs and evaluation logic are unchanged.
The setup helper now shares the extracted pure builder, preserving its previous
definition and connection creation behavior. Consumers using unversioned names
may observe the newest published version immediately; pin versions when
reproducibility is required.
Roll back consumers to a known prior version instead of modifying history.
Actual cloud publication and inference require separate authorization; neither
was performed while implementing this workflow. Immutable agent definitions do
not freeze the external connection target, model deployment or ontology state.
