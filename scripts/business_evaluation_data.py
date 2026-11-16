"""Prepare recorded business-evaluation cases and source provenance."""

import hashlib
import json
from pathlib import Path

from manual_agent_test import load_dataset, validate_fabric_handoff


def captured_cases(
    dataset_path: Path, source_paths: list[Path], endpoint: str, case_id: str | None = None,
) -> tuple[list[dict], dict]:
    if not source_paths:
        raise ValueError("Provide saved captures from one or two distinct agents")
    cases, reference_sha = load_dataset(dataset_path)
    selected = {case["id"]: case for case in cases if case_id is None or case["id"] == case_id}
    if not selected:
        raise ValueError(f"Unknown reference case: {case_id}")
    rows, sources = [], []
    agents = {}
    seen = set()
    for path in source_paths:
        raw = path.read_bytes()
        source = json.loads(raw)
        case = selected.get(source["case_id"])
        if (
            case is None or source["project_endpoint"] != endpoint
            or source["dataset_sha256"] != reference_sha
            or source["query"] != case["query"]
        ):
            raise ValueError(f"{path}: capture endpoint, reference or case differs")
        agent = source["agent"]
        key = (agent["name"], agent["version"])
        if not all(isinstance(value, str) and value for value in key) or not key[1].isdigit():
            raise ValueError(f"{path}: capture requires a pinned agent version")
        identity = (key, case["id"])
        if identity in seen:
            raise ValueError("Captured responses require distinct agent/case pairs")
        seen.add(identity)
        number = agents.setdefault(key, len(agents) + 1)
        if len(agents) > 2:
            raise ValueError("Provide captures from at most two distinct agents")
        response = source["response"]
        if source["status"] != "captured" or response.get("status") != "completed":
            raise ValueError(f"{path}: capture is incomplete or failed")
        validate_fabric_handoff(response, source["query"])
        answer = recorded_answer({"status": response["status"], "sample": response})
        rows.append({
            "id": f"A{number}-{case['id']}", "case_id": case["id"], "agent": agent,
            "origin": "recorded_agent", "source_response_id": response["id"],
            "response": answer,
            **{field: case[field] for field in ("query", "context", "ground_truth")},
            **{field: json.dumps(case[field], ensure_ascii=False)
               for field in ("criteria", "critical_errors")},
        })
        sources.append({
            "path": str(path.resolve()), "sha256": hashlib.sha256(raw).hexdigest(),
            "agent": agent, "case_id": case["id"], "response_id": response["id"],
        })
    if seen != {(agent, case) for agent in agents for case in selected}:
        raise ValueError("Captured responses are missing cases for an agent")
    return rows, {
        "mode": "comparison", "reference_sha256": reference_sha,
        "case_id": case_id, "sources": sources,
        "note": "Judge-only evaluation of saved responses with matching reference cases. "
                "No new agent inference; agent identities are not mapped to the judge.",
    }


def recorded_answer(item: dict) -> str:
    if item.get("status") != "completed" or item["sample"].get("error"):
        raise ValueError("Source completion is missing or errored")
    texts = []
    for message in item["sample"]["output"]:
        if message.get("role") != "assistant":
            continue
        content = message["content"]
        parts = json.loads(content) if isinstance(content, str) else content
        texts.extend(part["text"] for part in parts if part.get("type") == "output_text")
    answer = "\n".join(texts)
    if not answer.strip():
        raise ValueError("Source completion contains no assistant text")
    return answer


def regression_cases(dataset_path: Path, source_path: Path, controls_path: Path) -> tuple[list[dict], dict]:
    cases, reference_sha = load_dataset(dataset_path)
    source_raw = source_path.read_bytes()
    controls_raw = controls_path.read_bytes()
    source = json.loads(source_raw)
    controls = json.loads(controls_raw)
    case = next(case for case in cases if case["id"] == controls["case_id"])
    item = next(item for item in source["output_items"] if item["datasource_item"]["id"] == case["id"])
    for field in ("query", "context", "ground_truth"):
        if item["datasource_item"][field] != case[field]:
            raise ValueError(f"Historical B-001 {field} differs from current reference; review before replay")
    answer = recorded_answer(item)
    if "vier Wochen" not in answer:
        raise ValueError("Source response does not reproduce the known four-week regression")
    references = {
        key: case[key] for key in ("query", "context", "ground_truth")
    } | {
        key: json.dumps(case[key], ensure_ascii=False) for key in ("criteria", "critical_errors")
    }
    candidates = [
        {"id": "B-001-R01", "response": answer, "expected_pass": False, "origin": "recorded_agent"},
        *[control | {"origin": "synthetic_calibration"} for control in controls["controls"]],
    ]
    if len({candidate["id"] for candidate in candidates}) != len(candidates):
        raise ValueError("Regression IDs must be unique")
    for candidate in candidates:
        if type(candidate["expected_pass"]) is not bool or not candidate["response"].strip():
            raise ValueError("Regression candidates need a boolean label and nonempty response")
    rows = [references | candidate for candidate in candidates]
    provenance = {
        "reference_sha256": reference_sha,
        "source_sha256": hashlib.sha256(source_raw).hexdigest(),
        "controls_sha256": hashlib.sha256(controls_raw).hexdigest(),
        "source_run_id": source["run_id"],
        "source_agent": source["agent"],
        "source_case_id": case["id"],
        "note": "Recorded agent failure plus explicitly synthetic calibration controls; not a new agent benchmark.",
    }
    return rows, provenance


def comparison_cases(dataset_path: Path, source_paths: list[Path]) -> tuple[list[dict], dict]:
    loaded, reference_sha = load_dataset(dataset_path)
    cases = {case["id"]: case for case in loaded}
    rows = []
    sources = []
    source_agents = set()
    dataset_sha = None
    for number, path in enumerate(source_paths, 1):
        source_raw = path.read_bytes()
        source = json.loads(source_raw)
        agent = source["agent"]
        agent_key = (agent["name"], agent["version"])
        if agent_key in source_agents:
            raise ValueError("Comparison sources must contain distinct pinned agents")
        source_agents.add(agent_key)
        live = source["dataset"].get("mode") == "live"
        source_sha = (source["dataset"]["reference_sha256"] if live
                      else source["dataset"]["sha256"])
        if dataset_sha is None:
            dataset_sha = source_sha
        elif source_sha != dataset_sha:
            raise ValueError("Comparison sources used different datasets")
        if live and source_sha != reference_sha:
            raise ValueError(f"{path}: historical reference differs from current reference")
        items = source["output_items"]
        if len(items) != len(cases) or {item["datasource_item"]["id"] for item in items} != set(cases):
            raise ValueError(f"{path}: comparison requires exactly one response for every reference case")
        for item in items:
            case_id = item["datasource_item"]["id"]
            case = cases[case_id]
            for field in ("query", "context", "ground_truth"):
                if item["datasource_item"][field] != case[field]:
                    raise ValueError(f"{path}: historical {case_id} {field} differs from current reference")
            if live:
                answer = item["datasource_item"]["response"]
                if item.get("status") != "completed" or not isinstance(answer, str) or not answer.strip():
                    raise ValueError(f"{path}: source response missing or errored for {case_id}")
            else:
                answer = recorded_answer(item)
            rows.append({
                "id": f"A{number}-{case_id}", "case_id": case_id,
                "agent": agent, "origin": "recorded_agent",
                "source_run_id": source["run_id"],
                "response": answer,
                **{key: case[key] for key in ("query", "context", "ground_truth")},
                **{key: json.dumps(case[key], ensure_ascii=False)
                   for key in ("criteria", "critical_errors")},
            })
        sources.append({
            "path": str(path.resolve()), "sha256": hashlib.sha256(source_raw).hexdigest(),
            "run_id": source["run_id"], "agent": agent,
        })
    if len(source_agents) < 2:
        raise ValueError("Comparison requires at least two distinct agents")
    return rows, {
        "mode": "comparison", "reference_sha256": reference_sha,
        "source_dataset_sha256": dataset_sha, "sources": sources,
        "note": "Blind replay of recorded responses; agent names are not mapped to the judge. "
                "No new agent inference. Different agent knowledge is intentional.",
    }
