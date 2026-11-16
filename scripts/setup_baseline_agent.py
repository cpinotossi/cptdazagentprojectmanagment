"""Create a tool-free Foundry baseline with a static snapshot of the demo."""

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import requests
from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import AgentVersionDetails, PromptAgentDefinition
from azure.core.credentials import TokenCredential
from azure.identity import AzureCliCredential

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "agents/basic"
CONFIG = json.loads((COMPONENT / "config.json").read_text(encoding="utf-8"))
if (
    not isinstance(CONFIG, dict) or set(CONFIG) != {"name", "reference_agent"}
    or not all(isinstance(value, str) and value and value.strip() == value for value in CONFIG.values())
):
    raise ValueError("Basic config must contain nonempty name and reference_agent strings")
BASELINE_AGENT_NAME = CONFIG["name"]
AGENT_NAME = CONFIG["reference_agent"]
WORKSPACE_ID = "66d9c2b2-d35e-4555-9e74-926164ce584c"
SEMANTIC_MODEL_ID = "22fb9927-ac28-4092-80c0-0f3d7a56a156"
TABLES = ("projects", "tasks", "task_dependencies", "project_managers", "emails", "documents")
SUPPLY_TABLES = ("suppliers", "materials", "material_deliveries")
PORTFOLIO_TABLES = ("supplier_materials",)
SNAPSHOT_MARKER = "Static reference snapshot (JSON):\n"
POLICY = (COMPONENT / "policy.txt").read_text(encoding="utf-8")
if not POLICY.strip():
    raise ValueError("Committed Basic policy must not be empty")
POLICY += SNAPSHOT_MARKER


def headers(credential: TokenCredential, resource: str) -> dict[str, str]:
    token = credential.get_token(f"{resource}/.default").token
    return {"Authorization": f"Bearer {token}"}


def data_only_snapshot(snapshot: dict) -> dict:
    tables = snapshot.get("tables")
    if not isinstance(tables, dict) or set(tables) not in (
        set(TABLES), set(TABLES + SUPPLY_TABLES), set(TABLES + SUPPLY_TABLES + PORTFOLIO_TABLES),
    ):
        raise ValueError("Snapshot must contain the six project data tables, optionally with all three supply tables and their supplier portfolio")
    for table, rows in tables.items():
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise ValueError(f"Snapshot table {table} must contain a list of records")
    return {
        key: snapshot[key] for key in (
            "capture_started_at", "capture_completed_at", "workspace_id",
            "semantic_model_id", "capture_note", "tables",
        ) if key in snapshot
    }


def table_rows(
    session: requests.Session, credential: TokenCredential, table: str,
) -> list[dict]:
    if table not in TABLES + SUPPLY_TABLES + PORTFOLIO_TABLES:
        raise ValueError(f"Unexpected snapshot table: {table}")
    response = session.post(
        "https://api.powerbi.com/v1.0/myorg/"
        f"groups/{WORKSPACE_ID}/datasets/{SEMANTIC_MODEL_ID}/executeQueries",
        headers=headers(credential, "https://analysis.windows.net/powerbi/api"),
        json={
            "queries": [{"query": f"EVALUATE '{table}'"}],
            "serializerSettings": {"includeNulls": True},
        },
        timeout=120,
    )
    if not response.ok:
        raise RuntimeError(
            f"Snapshot query HTTP {response.status_code} for {table}: {response.text}"
        )
    body = response.json()
    if body.get("error"):
        raise RuntimeError(f"Snapshot query failed for {table}: {body['error']}")
    results = body["results"]
    if len(results) != 1 or results[0].get("error"):
        raise RuntimeError(f"Snapshot query failed for {table}: {results}")
    tables = results[0]["tables"]
    if len(tables) != 1 or tables[0].get("error"):
        raise RuntimeError(f"Snapshot table failed for {table}: {tables}")
    rows = tables[0]["rows"]
    if len(rows) >= 100_000:
        raise ValueError(f"Snapshot table {table} reached the executeQueries row limit")
    return sorted(rows, key=lambda row: json.dumps(row, sort_keys=True, ensure_ascii=False))


def capture_snapshot(
    session: requests.Session, credential: TokenCredential,
) -> dict:
    started = datetime.now(timezone.utc).isoformat()
    data = {}
    for table in TABLES + SUPPLY_TABLES + PORTFOLIO_TABLES:
        data[table] = table_rows(session, credential, table)
        print(f"Snapshot: {table}: {len(data[table])} rows", flush=True)
    return {
        "capture_started_at": started,
        "capture_completed_at": datetime.now(timezone.utc).isoformat(),
        "workspace_id": WORKSPACE_ID,
        "semantic_model_id": SEMANTIC_MODEL_ID,
        "capture_note": "Sequential live reads, not an atomic cross-table snapshot.",
        "tables": data,
    }


def baseline_definition(reference: AgentVersionDetails, snapshot: dict) -> PromptAgentDefinition:
    if not isinstance(reference.definition, PromptAgentDefinition):
        raise ValueError("Reference agent must have a prompt definition")
    source = reference.definition.as_dict()
    body = {
        key: source[key] for key in ("model", "temperature", "top_p", "reasoning", "text", "rai_config")
        if key in source
    }
    body.update({
        "kind": "prompt",
        "tools": [],
        "instructions": POLICY + json.dumps(data_only_snapshot(snapshot), ensure_ascii=False, sort_keys=True),
    })
    return PromptAgentDefinition(body)


def setup_baseline(
    project: AIProjectClient, session: requests.Session, credential: TokenCredential,
    output: Path, reference_version: str | None, snapshot_path: Path | None = None,
) -> dict:
    if reference_version:
        reference = project.agents.get_version(
            agent_name=AGENT_NAME, agent_version=reference_version,
        )
    else:
        reference = next(iter(project.agents.list_versions(
            agent_name=AGENT_NAME, limit=1, order="desc", include_drafts=False,
        )), None)
    if reference is None:
        raise ValueError(f"No published version found for reference agent {AGENT_NAME}")
    if snapshot_path is None:
        snapshot = data_only_snapshot(capture_snapshot(session, credential))
        snapshot_source = {"type": "live_semantic_model"}
    else:
        source_bytes = snapshot_path.read_bytes()
        snapshot = data_only_snapshot(json.loads(source_bytes)["snapshot"])
        snapshot_source = {
            "type": "saved_snapshot",
            "path": str(snapshot_path.resolve()),
            "sha256": hashlib.sha256(source_bytes).hexdigest(),
        }
    agent_definition = baseline_definition(reference, snapshot)
    digest = hashlib.sha256(agent_definition.instructions.encode("utf-8")).hexdigest()
    record = {
        "schema_version": 2,
        "reference_agent": {"name": reference.name, "version": reference.version},
        "instructions_sha256": digest,
        "definition": agent_definition.as_dict(),
        "snapshot": snapshot,
        "snapshot_source": snapshot_source,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    agent = project.agents.create_version(
        agent_name=BASELINE_AGENT_NAME,
        definition=agent_definition,
        metadata={
            "reference_agent": reference.name,
            "reference_version": reference.version,
            "snapshot_sha256": digest,
        },
        description="Data-only static baseline without Fabric IQ, tools or TMDL knowledge.",
    )
    record["agent"] = {"name": agent.name, "version": agent.version}
    output.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    saved = project.agents.get_version(agent_name=agent.name, agent_version=agent.version)
    if saved.definition.as_dict() != agent_definition.as_dict():
        raise RuntimeError(f"Agent readback differs from requested definition; see {output}")
    print(f"Agent: {agent.name} version {agent.version}; model: {agent_definition.model}; tools: 0")
    print(f"Reference: {reference.name} version {reference.version}")
    print(f"Snapshot and exact instructions: {output}")
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default=os.environ.get("FOUNDRY_PROJECT_ENDPOINT"))
    parser.add_argument("--reference-version", help="Pin the existing Fabric IQ agent version")
    parser.add_argument(
        "--snapshot", type=Path,
        help="Reuse table records from a saved baseline snapshot, discarding all TMDL metadata",
    )
    parser.add_argument(
        "--output", type=Path,
        default=ROOT / "evaluations/baselines" / (
            datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json"
        ),
    )
    args = parser.parse_args()
    if not args.endpoint:
        parser.error("--endpoint or FOUNDRY_PROJECT_ENDPOINT is required")
    if args.output.exists():
        parser.error(f"Output already exists; choose a new snapshot path: {args.output}")
    credential = AzureCliCredential()
    with requests.Session() as session, AIProjectClient(
        endpoint=args.endpoint, credential=credential,
    ) as project:
        setup_baseline(project, session, credential, args.output, args.reference_version, args.snapshot)


if __name__ == "__main__":
    main()
