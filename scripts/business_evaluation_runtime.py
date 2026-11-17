"""Run business evaluations through Foundry's existing-dataset agent target."""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import TestingCriterionAzureAIEvaluator
from azure.core.exceptions import ResourceNotFoundError
from openai import OpenAI
from openai.types.evals import RunCreateResponse

from evaluate_agent import save_result
from business_evaluation_datasets import register_file
from evaluate_agent import run_body
from foundry_datasets import REFERENCE_NAME
from manual_agent_test import capture_case, load_dataset

EVALUATOR_NAME = "project-planning-business-correctness"
EVALUATOR_PATH = Path(__file__).resolve().parents[1] / "evaluations/evaluators/business_correctness.json"
ANCHORS_NAME = "project-planning-business-anchors"
ANCHORS_PATH = Path(__file__).resolve().parents[1] / "evaluations/evaluators/business_anchors.py"
CORRECTNESS_CRITERION = "Business correctness"
ANCHORS_CRITERION = "Business anchors"


def evaluator_definition() -> dict:
    return json.loads(EVALUATOR_PATH.read_text(encoding="utf-8"))


def anchors_definition() -> dict:
    return {
        "type": "code",
        "code_text": ANCHORS_PATH.read_text(encoding="utf-8"),
        "init_parameters": {
            "type": "object", "required": ["deployment_name", "pass_threshold"],
            "properties": {"deployment_name": {"type": "string"}, "pass_threshold": {"type": "number"}},
        },
        "metrics": {"anchors": {
            "type": "ordinal", "desirable_direction": "increase", "min_value": 0.0, "max_value": 1.0,
        }},
        "data_schema": {
            "type": "object", "required": ["item"],
            "properties": {"item": {"type": "object", "properties": {"criteria": {"type": "string"}}}},
        },
    }


def ensure_anchors_evaluator(project: AIProjectClient) -> str:
    """Return the version whose code matches the workspace file, registering it when absent."""
    definition = anchors_definition()
    digest = hashlib.sha256(definition["code_text"].encode()).hexdigest()
    try:
        for version in project.evaluators.list_versions(name=ANCHORS_NAME):
            if (version.metadata or {}).get("code_sha256") == digest:
                return version.version
    except ResourceNotFoundError:
        pass
    created = project.evaluators.create_version(
        name=ANCHORS_NAME,
        evaluator_version={
            "name": ANCHORS_NAME,
            "display_name": "Project planning business anchors",
            "description": "Share of required IDs, e-mail addresses and numbers found in the generated answer; no LLM.",
            "evaluator_type": "custom",
            "categories": ["quality"],
            "supported_evaluation_levels": ["turn"],
            "metadata": {"code_sha256": digest},
            "definition": definition,
        },
        headers={"Foundry-Features": "Evaluations=V1Preview"},
    )
    saved = project.evaluators.get_version(name=ANCHORS_NAME, version=created.version)
    if saved.definition.as_dict().get("code_text") != definition["code_text"]:
        raise RuntimeError("Persisted Foundry anchor evaluator code differs from the workspace file")
    print(f"Foundry evaluator: {ANCHORS_NAME} version {created.version}", flush=True)
    return created.version


FIELDS = tuple(evaluator_definition()["data_schema"]["required"])


def capture_agent_cases(
    project: AIProjectClient, client: OpenAI, endpoint: str,
    dataset_path: Path, agent_name: str, agent_version: str, output_dir: Path,
) -> list[Path]:
    cases, reference_sha = load_dataset(dataset_path)
    agent = project.agents.get_version(agent_name=agent_name, agent_version=agent_version)
    paths = []
    for case in cases:
        path = output_dir / f"{case['id']}.capture.json"
        captured = capture_case(
            project, endpoint, agent.name, agent.version, case, reference_sha, path,
            human_review=False, overwrite=True, client=client, print_answer=False,
        )
        save_result(output_dir / f"{case['id']}.response.json", captured["response"])
        paths.append(path)
    return paths


def evaluation_body(
    judge_model: str, evaluator_version: str, *, evaluator_kind: str = "custom",
    anchors_version: str | None = None,
) -> dict:
    if evaluator_kind != "custom":
        raise ValueError("Direct dataset runs support the custom business evaluator only")
    fields = tuple(field for field in FIELDS if field != "response")
    criteria = [
        TestingCriterionAzureAIEvaluator(
            type="azure_ai_evaluator",
            name=CORRECTNESS_CRITERION,
            evaluator_name=EVALUATOR_NAME,
            evaluator_version=evaluator_version,
            initialization_parameters={
                "deployment_name": judge_model, "evaluation_level": "turn",
                "threshold": 0.5,
            },
            data_mapping={
                field: "{{sample.output_text}}" if field == "response" else "{{item." + field + "}}"
                for field in FIELDS
            },
        ),
    ]
    if anchors_version:
        criteria.append(TestingCriterionAzureAIEvaluator(
            type="azure_ai_evaluator",
            name=ANCHORS_CRITERION,
            evaluator_name=ANCHORS_NAME,
            evaluator_version=anchors_version,
            initialization_parameters={"deployment_name": judge_model, "pass_threshold": 1.0},
        ))
    return {
        "name": "Project planning - direct agent business evaluation",
        "data_source_config": {
            "type": "custom",
            "item_schema": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    **{field: {"type": "string"} for field in fields},
                },
                "required": ["id", *fields],
            },
            "include_sample_schema": True,
        },
        "testing_criteria": criteria,
    }


def create_agent_run(
    project: AIProjectClient, client: OpenAI, endpoint: str,
    dataset_path: Path, agent_name: str, agent_version: str, judge_model: str,
    evaluator_version: str | None = None,
    *, evaluator_kind: str = "custom", comparison_id: str | None = None,
    run_label: str | None = None,
) -> dict:
    if evaluator_kind != "custom":
        raise ValueError("Direct dataset runs support the custom business evaluator only")
    if dataset_path.suffix != ".jsonl":
        raise ValueError("Direct agent evaluation requires the canonical reference JSONL, not a JSON source")
    cases, reference_sha = load_dataset(dataset_path)
    for row in map(json.loads, dataset_path.read_text(encoding="utf-8").splitlines()):
        if "response" in row or not all(isinstance(row[field], str) for field in FIELDS if field != "response"):
            raise ValueError("Direct agent inputs must contain reference strings and no saved response")
    agent = project.agents.get_version(agent_name=agent_name, agent_version=agent_version)
    agent_ref = {"name": agent.name, "version": agent.version}
    rows = [
        {
            **{key: case[key] for key in ("id", "query", "context", "ground_truth")},
            **{key: json.dumps(case[key], ensure_ascii=False)
               for key in ("criteria", "critical_errors")},
            "case_id": case["id"], "agent": agent_ref,
        }
        for case in cases
    ]
    name = EVALUATOR_NAME
    definition = evaluator_definition()
    definition_sha = hashlib.sha256(json.dumps(definition, sort_keys=True).encode()).hexdigest()
    if evaluator_version:
        evaluator = project.evaluators.get_version(name=name, version=evaluator_version)
    else:
        evaluator = project.evaluators.create_version(
            name=name,
            evaluator_version={
                "name": name,
                "display_name": "Project planning business correctness",
                "description": "Binary mandatory-fact and critical-error evaluation, including escalation deadlines.",
                "evaluator_type": "custom",
                "categories": ["quality"],
                "supported_evaluation_levels": ["turn"],
                "metadata": {"definition_sha256": definition_sha},
                "definition": definition,
            },
            headers={"Foundry-Features": "Evaluations=V1Preview"},
        )
    saved = project.evaluators.get_version(name=name, version=evaluator.version)
    persisted = saved.definition.as_dict()
    if persisted != definition:
        raise RuntimeError("Persisted Foundry evaluator definition differs from requested definition")
    print(f"Foundry evaluator: {evaluator.name} version {evaluator.version}", flush=True)
    anchors_version = ensure_anchors_evaluator(project)
    body = evaluation_body(
        judge_model, evaluator.version, evaluator_kind=evaluator_kind, anchors_version=anchors_version,
    )
    case_ids = sorted(case["id"] for case in cases)
    scope = case_ids[0] if len(case_ids) == 1 else f"{case_ids[0]}..{case_ids[-1]}"
    agent_label = f"{agent.name} v{agent.version}"
    display_label = f"{run_label} | {agent_label}" if run_label else agent_label
    body["name"] = (
        f"{display_label} | {scope} | "
        f"{evaluator_kind} v{evaluator.version} | ref {reference_sha[:8]}"
    )
    configuration_sha = hashlib.sha256(json.dumps(
        {"evaluation": body, "reference_sha256": reference_sha}, sort_keys=True,
    ).encode()).hexdigest()
    body["metadata"] = {
        "workflow": "project-planning-business-v1",
        "configuration_sha256": configuration_sha,
    }
    evaluation = None
    for existing in client.evals.list(limit=100, order="desc"):
        if existing.metadata and all(
            existing.metadata.get(key) == value for key, value in body["metadata"].items()
        ):
            evaluation = client.evals.retrieve(existing.id).model_dump(mode="json")
            break
    if evaluation is None:
        evaluation = client.post("/evals", cast_to=dict, body=body)
    if not isinstance(evaluation, dict) or not isinstance(evaluation.get("id"), str) or not evaluation["id"]:
        raise TypeError("Unexpected evaluation creation response")
    dataset = register_file(project, REFERENCE_NAME, dataset_path)
    comparison_id = comparison_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    body = run_body(dataset["id"], agent.name, agent.version)
    body["name"] = f"{display_label} | {comparison_id}"
    body["metadata"].update(
        comparison_id=comparison_id, agents=agent_label, reference_sha256=reference_sha,
    )
    run = client.post(
        f"/evals/{evaluation['id']}/runs", cast_to=RunCreateResponse,
        body=body,
    )
    if not isinstance(run, RunCreateResponse):
        raise TypeError("Unexpected evaluation run creation response")
    record = {
        "schema_version": 1,
        "project_endpoint": endpoint,
        "agent": agent_ref,
        "dataset": {
            **dataset, "source": str(dataset_path.resolve()),
            "case_count": len(cases), "cases": rows, "mode": "live",
            "execution": "azure_ai_target_completions", "reference_sha256": reference_sha,
            "note": "Foundry invokes the pinned agent from the existing reference dataset and judges "
                    "its generated answers. Only query reaches the agent; no inline inputs or local captures.",
        },
        "comparison_id": comparison_id,
        "evaluator": {
            "kind": evaluator_kind, "name": evaluator.name, "version": evaluator.version,
            "judge_model": judge_model, "definition_sha256": definition_sha, "definition": persisted,
            "anchors": {"name": ANCHORS_NAME, "version": anchors_version},
        },
        "evaluation_id": evaluation["id"], "run_id": run.id,
        "evaluation": evaluation,
        "run": run.model_dump(mode="json"),
        "output_items": [],
    }
    return record


def create_regression_run(
    project: AIProjectClient, client: OpenAI, endpoint: str,
    rows: list[dict], provenance: dict, judge_model: str,
    evaluator_version: str | None = None,
    *, evaluator_kind: str = "custom", comparison_id: str | None = None,
    run_label: str | None = None,
) -> dict:
    raise ValueError(
        "Cloud judging of saved answers and calibration controls is disabled: "
        "inline inputs create additional Foundry datasets. Use a direct --agent run instead."
    )
