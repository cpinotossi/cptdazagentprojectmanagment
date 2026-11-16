"""Generate, check and publish the versioned JSONL datasets in a JSON catalog."""

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from azure.ai.projects import AIProjectClient

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "evaluations/datasets.json"
REFERENCE_NAME = "project-planning-business-reference-cases"
REFERENCE_SOURCE = ROOT / "evaluations/business_tests.json"
REFERENCE_PATH = ROOT / "evaluations/generated/business_reference.jsonl"
CHECK_FIELDS = ("id", "query", "context", "ground_truth")
JUDGE_FIELDS = ("query", "context", "ground_truth", "criteria", "critical_errors")


def catalog_path(base: Path, value: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError("Catalog paths must be nonempty strings")
    path = (base / value).resolve()
    if not path.is_relative_to(base.resolve()):
        raise ValueError(f"Catalog path escapes its directory: {value}")
    return path


def read_cases(path: Path) -> list[dict]:
    cases = json.loads(path.read_text(encoding="utf-8"))["test_cases"]
    if not isinstance(cases, list) or not cases:
        raise ValueError(f"{path}: test_cases must be a nonempty array")
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError(f"{path}: every case must be an object")
        for field in CHECK_FIELDS:
            if not isinstance(case.get(field), str) or not case[field].strip():
                raise ValueError(f"{path}: missing or empty {field}")
        if "response" in case:
            raise ValueError(f"{path}: reference cases must not contain agent responses")
        for field in ("criteria", "critical_errors"):
            items = case.get(field)
            if not isinstance(items, list) or not items:
                raise ValueError(f"{path}: {case['id']} needs nonempty {field}")
            ids = []
            for item in items:
                if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]:
                    raise ValueError(f"{path}: invalid {field} ID")
                if not isinstance(item.get("description"), str) or not item["description"].strip():
                    raise ValueError(f"{path}: invalid {field} description")
                ids.append(item["id"])
            if len(set(ids)) != len(ids):
                raise ValueError(f"{path}: duplicate {field} IDs")
    return cases


def unique_rows(rows: list[dict], name: str) -> list[dict]:
    if not rows or len({row["id"] for row in rows}) != len(rows):
        raise ValueError(f"{name}: dataset must have nonempty, unique row IDs")
    return sorted(rows, key=lambda row: row["id"])


def jsonl_bytes(rows: list[dict]) -> bytes:
    return "".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n"
        for row in unique_rows(rows, "JSONL")
    ).encode("utf-8")


def reference_rows(cases: list[dict]) -> list[dict]:
    return [
        {**case, **{
            field: json.dumps(case[field], ensure_ascii=False, sort_keys=True)
            for field in ("criteria", "critical_errors")
        }}
        for case in cases
    ]


def render_datasets(catalog: Path) -> list[dict]:
    definition = json.loads(catalog.read_text(encoding="utf-8"))
    if definition.get("schema_version") != 1 or not definition.get("datasets"):
        raise ValueError("Catalog requires schema_version 1 and nonempty datasets")
    base = catalog.parent.resolve()
    rendered = []
    names, outputs, sources = set(), set(), set()
    for entry in definition["datasets"]:
        name, kind = entry["name"], entry["kind"]
        if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,127}", name):
            raise ValueError(f"Invalid dataset name: {name}")
        if name in names:
            raise ValueError(f"Duplicate dataset name: {name}")
        names.add(name)
        paths = entry["sources"]
        if not isinstance(paths, list) or not paths:
            raise ValueError(f"{name}: sources must be a nonempty array")
        input_paths = [catalog_path(base, value) for value in paths]
        sources.update(input_paths)
        output = catalog_path(base, entry["output"])
        if output.suffix != ".jsonl" or output in outputs:
            raise ValueError(f"{name}: output must be a unique JSONL path")
        outputs.add(output)
        rows = []
        if kind == "cases":
            cases = [case for path in input_paths for case in read_cases(path)]
            rows = reference_rows(cases)
        elif kind == "calibration":
            reference = catalog_path(base, entry["reference"])
            sources.add(reference)
            cases = {case["id"]: case for case in unique_rows(read_cases(reference), name)}
            for path in input_paths:
                controls = json.loads(path.read_text(encoding="utf-8"))
                case = cases.get(controls["case_id"])
                if case is None:
                    raise ValueError(f"{path}: unknown reference case {controls['case_id']}")
                for control in controls["controls"]:
                    if (
                        not isinstance(control.get("id"), str) or not control["id"]
                        or type(control.get("expected_pass")) is not bool
                        or not isinstance(control.get("response"), str) or not control["response"].strip()
                    ):
                        raise ValueError(f"{path}: invalid calibration control")
                    rows.append({
                        "id": control["id"], "response": control["response"],
                        **{field: (
                            json.dumps(case[field], ensure_ascii=False)
                            if field in ("criteria", "critical_errors") else case[field]
                        ) for field in JUDGE_FIELDS},
                    })
        else:
            raise ValueError(f"{name}: unsupported dataset kind {kind}")
        rows = unique_rows(rows, name)
        content = jsonl_bytes(rows)
        rendered.append({
            "name": name, "kind": kind, "path": output, "content": content,
            "row_count": len(rows), "sha256": hashlib.sha256(content).hexdigest(),
        })
    if catalog.resolve() in outputs or outputs & sources:
        raise ValueError("Generated outputs must not overwrite the catalog or its sources")
    return rendered


def check_datasets(items: list[dict]) -> None:
    stale = [str(item["path"]) for item in items
             if not item["path"].is_file() or item["path"].read_bytes() != item["content"]]
    if stale:
        raise ValueError("Missing or stale JSONL; run foundry_datasets.py generate:\n" + "\n".join(stale))


def publish_datasets(project: "AIProjectClient", items: list[dict], manifest: Path, endpoint: str) -> dict:
    from business_evaluation_datasets import register_file
    from evaluate_agent import save_result

    check_datasets(items)
    if manifest.resolve() in {item["path"] for item in items}:
        raise ValueError("Release manifest must not overwrite a dataset")
    record = {
        "schema_version": 1, "project_endpoint": endpoint,
        "git_sha": os.environ.get("GITHUB_SHA"), "status": "publishing", "datasets": [],
    }
    save_result(manifest, record)
    for item in items:
        asset = register_file(project, item["name"], item["path"])
        if asset["sha256"] != item["sha256"]:
            raise ValueError(f"{item['name']}: JSONL changed during publication")
        saved = project.datasets.get(name=asset["name"], version=asset["version"])
        if saved.id != asset["id"]:
            raise RuntimeError(f"{item['name']}: dataset readback ID differs")
        record["datasets"].append({
            **asset, "kind": item["kind"], "row_count": item["row_count"],
        })
        save_result(manifest, record)
    record["status"] = "published"
    save_result(manifest, record)
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("generate", "check", "publish"))
    parser.add_argument("--catalog", type=Path, default=CATALOG)
    parser.add_argument("--dataset", action="append", help="Select names for publication; repeatable")
    parser.add_argument("--endpoint", default=os.environ.get("FOUNDRY_PROJECT_ENDPOINT"))
    parser.add_argument("--manifest", type=Path, default=ROOT / "evaluations/releases/datasets.json")
    parser.add_argument("--confirm", action="store_true", help="Authorize creating Foundry dataset versions")
    args = parser.parse_args()
    items = render_datasets(args.catalog)
    if args.operation == "generate":
        for item in items:
            item["path"].parent.mkdir(parents=True, exist_ok=True)
            item["path"].write_bytes(item["content"])
    else:
        check_datasets(items)
    if args.dataset:
        selected = set(args.dataset)
        if selected - {item["name"] for item in items}:
            parser.error("--dataset contains names absent from the catalog")
        items = [item for item in items if item["name"] in selected]
    if args.operation == "publish":
        if not args.confirm or not args.endpoint:
            parser.error("publish requires --confirm and --endpoint or FOUNDRY_PROJECT_ENDPOINT")
        if args.manifest.resolve().is_relative_to(ROOT) and not any(
            args.manifest.resolve().is_relative_to(ROOT / "evaluations" / directory)
            for directory in ("releases", "notebook_runs", "manual_runs")
        ):
            parser.error("Keep repository-local release manifests in an ignored output directory")
        from azure.ai.projects import AIProjectClient
        from azure.identity import AzureCliCredential

        with AIProjectClient(endpoint=args.endpoint, credential=AzureCliCredential()) as project:
            publish_datasets(project, items, args.manifest, args.endpoint)
    for item in items:
        print(f"{item['name']}: {item['row_count']} rows, version {item['sha256'][:16]}")


if __name__ == "__main__":
    main()
