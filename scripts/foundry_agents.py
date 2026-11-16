"""Check or publish both existing agents without provisioning or inference."""

import argparse
import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import PromptAgentDefinition
from azure.identity import AzureCliCredential

from setup_agent import AGENT_NAME, INSTRUCTIONS, ONTOLOGY_CONNECTION, agent_definition
from setup_baseline_agent import (
    AGENT_NAME as BASELINE_REFERENCE_AGENT_NAME,
    BASELINE_AGENT_NAME,
    POLICY,
    SNAPSHOT_MARKER,
    baseline_definition,
    data_only_snapshot,
)

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "agents/fabric-iq/config.json"


def digest(value: dict) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def check() -> str:
    if BASELINE_REFERENCE_AGENT_NAME != AGENT_NAME:
        raise ValueError("Basic reference_agent must identify the Fabric IQ agent released alongside it")
    instructions = INSTRUCTIONS.read_text(encoding="utf-8")
    if not instructions.strip():
        raise ValueError("Committed Fabric IQ instructions must not be empty")
    return instructions


def load_config(path: Path = CONFIG) -> dict:
    config = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(config, dict) or set(config) != {"endpoint", "model", "connection_id"}:
        raise ValueError("Release config must contain exactly endpoint, model and connection_id")
    if not all(isinstance(value, str) and value.strip() == value and value for value in config.values()):
        raise ValueError("Release configuration values must be nonempty strings without surrounding whitespace")
    endpoint = urlparse(config["endpoint"])
    connection = re.fullmatch(
        r"/subscriptions/[^/]+/resourceGroups/[^/]+/providers/Microsoft.CognitiveServices/"
        r"accounts/([^/]+)/projects/([^/]+)/connections/" + re.escape(ONTOLOGY_CONNECTION),
        config["connection_id"],
    )
    if (
        connection is None or endpoint.scheme != "https" or endpoint.username or endpoint.password
        or endpoint.query or endpoint.fragment
        or endpoint.hostname != f"{connection[1]}.services.ai.azure.com"
        or endpoint.path != f"/api/projects/{connection[2]}" or endpoint.port is not None
    ):
        raise ValueError("Release endpoint and existing project connection ARM ID must identify the same project")
    return config


def require_version(version: str) -> str:
    if not re.fullmatch(r"[1-9][0-9]*", version):
        raise ValueError("Pin an explicit positive numeric published version, not latest or a draft")
    return version


def snapshot_from_agent(source) -> dict:
    definition = source.definition
    if not isinstance(definition, PromptAgentDefinition) or definition.tools:
        raise ValueError("Baseline source must be a tool-free prompt agent")
    instructions = definition.instructions or ""
    prefix, marker, payload = instructions.partition(SNAPSHOT_MARKER)
    if not marker or not prefix.strip():
        raise ValueError("Baseline source lacks the explicit static-snapshot marker; review/migrate it explicitly")
    snapshot = json.loads(payload)
    if not isinstance(snapshot, dict):
        raise ValueError("Baseline snapshot must be a JSON object")
    cleaned = data_only_snapshot(snapshot)
    if snapshot != cleaned:
        raise ValueError("Baseline source contains unsupported metadata, not a data-only snapshot")
    if not cleaned["tables"]["projects"]:
        raise ValueError("Baseline source has no project evidence; empty baselines cannot be released")
    for field in ("workspace_id", "semantic_model_id", "capture_note"):
        if not isinstance(cleaned.get(field), str) or not cleaned[field].strip():
            raise ValueError(f"Baseline source must preserve {field}")
    times = []
    for field in ("capture_started_at", "capture_completed_at"):
        timestamp = cleaned.get(field)
        if not isinstance(timestamp, str) or not timestamp:
            raise ValueError(f"Baseline source must preserve {field}")
        value = datetime.fromisoformat(timestamp)
        if value.tzinfo is None:
            raise ValueError(f"Baseline {field} must have a timezone")
        times.append(value)
    if times[1] < times[0]:
        raise ValueError("Baseline capture interval is reversed")
    return cleaned


def release_definitions(config: dict, baseline_source) -> dict[str, PromptAgentDefinition]:
    definition = agent_definition(config["model"], config["connection_id"])
    snapshot = snapshot_from_agent(baseline_source)
    baseline = baseline_definition(SimpleNamespace(definition=definition), snapshot)
    return {AGENT_NAME: definition, BASELINE_AGENT_NAME: baseline}


def verify(agent, name: str, definition: PromptAgentDefinition, expected_version: str | None = None) -> None:
    if agent.name != name:
        raise RuntimeError(f"Readback returned the wrong agent name for {name}")
    require_version(agent.version)
    if expected_version is not None and agent.version != expected_version:
        raise RuntimeError(f"Readback returned the wrong version for {name}")
    if agent.definition.as_dict() != definition.as_dict():
        raise RuntimeError(f"Definition readback differs for {name} version {agent.version}")
    if agent.draft or agent.status != "active":
        raise RuntimeError(f"Agent {name} version {agent.version} is not an active published version")


def publish(project, config: dict, baseline_source_version: str, manifest: Path) -> dict:
    baseline_source_version = require_version(baseline_source_version)
    baseline_source = project.agents.get_version(
        agent_name=BASELINE_AGENT_NAME, agent_version=baseline_source_version,
    )
    if (baseline_source.name, baseline_source.version) != (BASELINE_AGENT_NAME, baseline_source_version):
        raise RuntimeError("Baseline source readback identity differs")
    verify(baseline_source, BASELINE_AGENT_NAME, baseline_source.definition, baseline_source_version)
    definitions = release_definitions(config, baseline_source)
    # Resolve committed configuration and frozen evidence before creating either version.
    record = {
        "schema_version": 1,
        "status": "publishing",
        "commit": os.environ.get("GITHUB_SHA"),
        "configuration": config,
        "configuration_sha256": digest(config),
        "sources": {
            BASELINE_AGENT_NAME: {
                "version": baseline_source_version,
                "definition_sha256": digest(baseline_source.definition.as_dict()),
            },
        },
        "agents": [],
    }

    def save() -> None:
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")

    save()
    for name, definition in definitions.items():
        sha256 = digest(definition.as_dict())
        existing = next((
            agent for agent in project.agents.list_versions(
                agent_name=name, order="desc", include_drafts=False,
            )
            if (agent.metadata or {}).get("release_definition_sha256") == sha256
        ), None)
        if existing is None:
            agent = project.agents.create_version(
                agent_name=name, definition=definition,
                metadata={
                    "release_definition_sha256": sha256,
                    "baseline_source_version": baseline_source_version,
                },
                description="Repository agent release; pinned data-only baseline source; no inference.",
                draft=False,
            )
            verify(agent, name, definition)
            reused = False
        else:
            agent = existing
            verify(agent, name, definition)
            reused = True
        saved = project.agents.get_version(agent_name=name, agent_version=agent.version)
        verify(saved, name, definition, agent.version)
        record["agents"].append({
            "name": name, "version": saved.version, "definition_sha256": sha256, "reused": reused,
        })
        save()
    record["status"] = "published"
    save()
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("check", "publish"), nargs="?", default="check")
    parser.add_argument("--config", type=Path, default=CONFIG)
    parser.add_argument("--baseline-source-version", default="")
    parser.add_argument("--confirm", action="store_true")
    parser.add_argument("--manifest", type=Path, default=ROOT / "evaluations/releases/agents.json")
    args = parser.parse_args()
    config = load_config(args.config)
    instructions = check()
    definition = agent_definition(config["model"], config["connection_id"])
    if args.operation == "check":
        print(json.dumps({
            "operation": "check", "cloud_access": False,
            "agents": [AGENT_NAME, BASELINE_AGENT_NAME],
            "instructions_sha256": hashlib.sha256(instructions.encode("utf-8")).hexdigest(),
            "configuration": config,
            "definition_sha256": digest(definition.as_dict()),
            "baseline_source": "Explicit existing published baseline version required at publish time",
        }, indent=2))
        return
    if not args.confirm:
        parser.error("publish requires --confirm")
    require_version(args.baseline_source_version)
    with AzureCliCredential() as credential, AIProjectClient(
        endpoint=config["endpoint"], credential=credential,
    ) as project:
        record = publish(project, config, args.baseline_source_version, args.manifest)
    print(json.dumps(record, indent=2))


if __name__ == "__main__":
    main()
