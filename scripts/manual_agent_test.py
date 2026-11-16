"""Capture direct agent responses and summarize explicit human business reviews."""

import argparse
import hashlib
import json
import re
import time
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path

from azure.ai.projects import AIProjectClient
from azure.core.exceptions import HttpResponseError
from azure.identity import AzureCliCredential
from openai import OpenAI, OpenAIError

from evaluate_agent import save_result
from foundry_datasets import REFERENCE_PATH, REFERENCE_SOURCE

ROOT = Path(__file__).resolve().parents[1]
DATASET = REFERENCE_PATH


def load_dataset(path: Path) -> tuple[list[dict], str]:
    raw = path.read_bytes()
    if path.suffix == ".jsonl":
        cases = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
        for case in cases:
            for field in ("criteria", "critical_errors"):
                if isinstance(case.get(field), str):
                    case[field] = json.loads(case[field])
    else:
        cases = json.loads(raw)["test_cases"]
    if not cases or len({case["id"] for case in cases}) != len(cases):
        raise ValueError("Dataset must have nonempty, unique cases")
    for case in cases:
        if not re.fullmatch(r"B-\d{3}", case["id"]):
            raise ValueError(f"Invalid case ID: {case['id']}")
        for field in ("query", "context", "ground_truth", "criteria", "critical_errors"):
            if not case.get(field):
                raise ValueError(f"{case['id']}: missing {field}")
        for field in ("criteria", "critical_errors"):
            ids = [item["id"] for item in case[field]]
            if len(set(ids)) != len(ids):
                raise ValueError(f"{case['id']}: duplicate {field} IDs")
    return cases, hashlib.sha256(raw).hexdigest()


def response_path(root: Path, agent: str, version: str, case_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", agent) or not re.fullmatch(r"\d+", version):
        raise ValueError("Use an agent name and an explicit numeric published version")
    return root / f"{agent}-v{version}" / f"{case_id}.json"


def review_template(case: dict) -> dict:
    return {
        "facts": {
            item["id"]: {"status": "unreviewed", "evidence": ""}
            for item in case["criteria"]
        },
        "critical_errors": {
            item["id"]: {"present": None, "evidence": ""}
            for item in case["critical_errors"]
        },
        "source_support": {"status": "unreviewed", "evidence": ""},
        "additional_incorrect_claims": None,
        "notes": "",
    }


def validate_fabric_handoff(response: dict, query: str) -> None:
    if not any(tool.get("type") == "fabric_iq_preview" for tool in response.get("tools", [])):
        return
    calls = [
        item for item in response.get("output", [])
        if item.get("type") == "mcp_call" and item.get("name") == "ask_ontology"
    ]
    if not calls:
        raise ValueError("Fabric IQ handoff missing: no ask_ontology call for the business query")
    try:
        arguments = json.loads(calls[0]["arguments"])
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise ValueError("Fabric IQ handoff contains invalid ask_ontology arguments") from error
    request = arguments.get("request") if isinstance(arguments, dict) else None
    if not isinstance(request, str) or " ".join(request.split()) != " ".join(query.split()):
        raise ValueError(
            "Fabric IQ handoff does not contain the complete original user message "
            "in the first ask_ontology request; business evaluation is blocked"
        )


def validate_capture_handoff(record: dict, path: Path) -> None:
    try:
        validate_fabric_handoff(record["response"], record["query"])
    except ValueError as error:
        record.update(status="error", error={"type": type(error).__name__, "message": str(error)})
        save_result(path, record)
        raise


def capture_case(
    project: AIProjectClient, endpoint: str, agent: str, version: str,
    case: dict, dataset_sha: str, path: Path,
    *, human_review: bool = True, overwrite: bool = False,
    client: OpenAI | None = None, print_answer: bool = True,
) -> dict:
    identity = {
        "project_endpoint": endpoint, "agent": {"name": agent, "version": version},
        "case_id": case["id"], "dataset_sha256": dataset_sha, "query": case["query"],
    }
    if path.exists() and not overwrite:
        existing = json.loads(path.read_text(encoding="utf-8"))
        if any(existing.get(key) != value for key, value in identity.items()):
            raise ValueError(f"{path}: different dataset, query, endpoint or agent; use a new run directory")
        if existing["status"] != "captured":
            raise ValueError(f"{path}: previous capture incomplete or failed; use a new run directory")
        validate_capture_handoff(existing, path)
        print(f"Reusing saved response, no inference: {path}", flush=True)
        return existing
    record = {
        "schema_version": 1, **identity, "status": "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "rubric": {key: case[key] for key in ("ground_truth", "criteria", "critical_errors", "sources")},
    }
    if human_review:
        record["review"] = review_template(case)
    save_result(path, record)
    started = time.monotonic()
    try:
        with (nullcontext(client) if client is not None else project.get_openai_client()) as response_client:
            raw_response = response_client.responses.with_raw_response.create(
                input=case["query"],
                extra_body={"agent_reference": {
                    "type": "agent_reference", "name": agent, "version": version,
                }},
            )
            response_json = raw_response.http_response.json()
            response = raw_response.parse()
    except (OpenAIError, HttpResponseError) as error:
        record.update(status="error", error={"type": type(error).__name__, "message": str(error)})
        save_result(path, record)
        raise
    record.update(
        elapsed_seconds=round(time.monotonic() - started, 3),
        completed_at=datetime.now(timezone.utc).isoformat(),
        response=response_json,
        answer=response.output_text,
    )
    if response.status != "completed" or not response.output_text.strip():
        record.update(status="error", error={"message": "Agent response incomplete or empty"})
        save_result(path, record)
        raise RuntimeError(f"Incomplete or empty agent response: {path}")
    validate_capture_handoff(record, path)
    record["status"] = "captured"
    save_result(path, record)
    if print_answer:
        print(f"\n{agent} v{version}, {case['id']} ({record['elapsed_seconds']} s)\n{record['answer']}")
    print(f"{'Human review required' if human_review else 'Response saved'}: {path}", flush=True)
    return record


def assess_review(case: dict, review: dict) -> dict:
    facts = review["facts"]
    errors = review["critical_errors"]
    if set(facts) != {item["id"] for item in case["criteria"]}:
        raise ValueError(f"{case['id']}: review facts do not match the dataset")
    if set(errors) != {item["id"] for item in case["critical_errors"]}:
        raise ValueError(f"{case['id']}: review critical errors do not match the dataset")
    items = [*facts.values(), review["source_support"]]
    allowed = {"unreviewed", "correct", "incorrect", "missing"}
    for item in items:
        if item["status"] not in allowed:
            raise ValueError(f"Invalid review status: {item['status']}")
        if item["status"] != "unreviewed" and not item["evidence"].strip():
            raise ValueError("Every reviewed item needs answer evidence or a missing-fact explanation")
    for error in errors.values():
        if error["present"] is not None and type(error["present"]) is not bool:
            raise ValueError("Critical error present must be true, false or null")
        if error["present"] is True and not error["evidence"].strip():
            raise ValueError("A critical error needs answer evidence")
    extra = review["additional_incorrect_claims"]
    if extra is not None and (
        not isinstance(extra, list) or not all(isinstance(item, str) and item.strip() for item in extra)
    ):
        raise ValueError("Additional incorrect claims must be null or a list of nonempty descriptions")
    correct = sum(item["status"] == "correct" for item in facts.values())
    incorrect = sum(item["status"] == "incorrect" for item in facts.values())
    missing = sum(item["status"] == "missing" for item in facts.values())
    pending = (
        any(item["status"] == "unreviewed" for item in items)
        or any(error["present"] is None for error in errors.values())
        or extra is None
    )
    critical = [key for key, value in errors.items() if value["present"] is True]
    failed = (
        incorrect > 0 or missing > 0 or bool(critical) or bool(extra)
        or review["source_support"]["status"] in {"incorrect", "missing"}
    )
    return {
        "outcome": "pending" if pending else ("failed" if failed else "passed"),
        "correct_facts": correct, "incorrect_facts": incorrect, "missing_facts": missing,
        "total_facts": len(facts),
        "fulfilled_fraction": correct / len(facts),
        "answered_fact_accuracy": correct / (correct + incorrect) if correct + incorrect else None,
        "critical_errors": critical,
        "dimensions": {
            dimension: {
                "correct": sum(facts[item["id"]]["status"] == "correct" for item in case["criteria"]
                               if item["dimension"] == dimension),
                "total": sum(item["dimension"] == dimension for item in case["criteria"]),
            }
            for dimension in sorted({item["dimension"] for item in case["criteria"]})
        },
    }


def summarize(root: Path, cases: list[dict], digest: str, agents: list[str]) -> dict:
    results = []
    for reference in agents:
        agent, version = reference.rsplit(":", 1)
        for case in cases:
            path = response_path(root, agent, version, case["id"])
            result = {"agent": reference, "case_id": case["id"], "path": str(path)}
            if not path.exists():
                result["outcome"] = "missing_response"
            else:
                record = json.loads(path.read_text(encoding="utf-8"))
                if (
                    record["dataset_sha256"] != digest or record["case_id"] != case["id"]
                    or record["agent"] != {"name": agent, "version": version}
                    or record["query"] != case["query"]
                ):
                    raise ValueError(f"{path}: capture does not match the selected dataset and agent")
                if record["status"] != "captured":
                    result["outcome"] = "capture_error"
                else:
                    result.update(assess_review(case, record["review"]))
            results.append(result)
    counts = {
        status: sum(item["outcome"] == status for item in results)
        for status in ("passed", "failed", "pending", "missing_response", "capture_error")
    }
    return {
        "dataset_sha256": digest, "counts": counts, "cases": results,
        "complete": not any(counts[key] for key in ("pending", "missing_response", "capture_error")),
        "note": "Human decisions only. Fact accuracy excludes omissions; fulfilled fraction includes them. "
                "Neither score overrides critical errors, unsupported claims or missing source support.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    capture = commands.add_parser("capture", help="Direct Responses API calls, no Foundry evaluation")
    capture.add_argument("--endpoint", required=True)
    capture.add_argument("--agent", required=True)
    capture.add_argument("--agent-version", required=True)
    capture.add_argument("--case", help="Omit to capture all dataset cases")
    capture.add_argument("--output-dir", type=Path, required=True)
    summary = commands.add_parser("summarize", help="Summarize edited human review sheets")
    summary.add_argument("--input-dir", type=Path, required=True)
    summary.add_argument("--agents", nargs="+", required=True, help="Pinned name:version pairs")
    export = commands.add_parser("export", help="Generate the canonical Foundry JSONL")
    export.add_argument("--output", type=Path, default=REFERENCE_PATH)
    validate = commands.add_parser("validate-capture", help="Reject an incomplete Fabric IQ handoff in a saved capture")
    validate.add_argument("path", type=Path)
    args = parser.parse_args()
    args.dataset = args.dataset or (REFERENCE_SOURCE if args.command == "export" else DATASET)
    if args.command == "validate-capture":
        record = json.loads(args.path.read_text(encoding="utf-8"))
        if record["status"] != "captured" or record["response"].get("status") != "completed":
            raise ValueError("Capture is incomplete or failed")
        validate_capture_handoff(record, args.path)
        print(f"Capture handoff checked: {args.path} (response {record['response']['id']})")
        return
    cases, digest = load_dataset(args.dataset)
    if args.command == "export":
        from foundry_datasets import jsonl_bytes, reference_rows

        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(jsonl_bytes(reference_rows(cases)))
        print(f"Exported {len(cases)} cases to {args.output}")
    elif args.command == "summarize":
        report = summarize(args.input_dir, cases, digest, args.agents)
        save_result(args.input_dir / "summary.json", report)
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        selected = [case for case in cases if not args.case or case["id"] == args.case]
        if not selected:
            parser.error(f"Unknown case: {args.case}")
        response_path(args.output_dir, args.agent, args.agent_version, selected[0]["id"])
        with AIProjectClient(endpoint=args.endpoint, credential=AzureCliCredential()) as project:
            for case in selected:
                capture_case(
                    project, args.endpoint, args.agent, args.agent_version, case, digest,
                    response_path(args.output_dir, args.agent, args.agent_version, case["id"]),
                )


if __name__ == "__main__":
    main()
