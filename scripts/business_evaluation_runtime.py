"""Register evaluators and submit live or recorded business-evaluation runs."""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import TestingCriterionAzureAIEvaluator
from openai import OpenAI
from openai.types.evals import RunCreateResponse

from evaluate_agent import save_result
from business_evaluation_datasets import register_evaluation_data
from business_rubric_evaluator import RUBRIC_EVALUATOR_NAME, rubric_definition
from manual_agent_test import capture_case, load_dataset, response_path

EVALUATOR_NAME = "project-planning-business-correctness"
EVALUATOR_PATH = Path(__file__).resolve().parents[1] / "evaluations/evaluators/business_correctness.json"


def evaluator_definition() -> dict:
    return json.loads(EVALUATOR_PATH.read_text(encoding="utf-8"))


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
) -> dict:
    rubric = evaluator_kind == "rubric"
    fields = (*FIELDS, "rubric_query") if rubric else FIELDS
    return {
        "name": "Project planning - B-001 business judge regression",
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
        },
        "testing_criteria": [
            TestingCriterionAzureAIEvaluator(
                type="azure_ai_evaluator",
                name="Business rubric" if rubric else "Business correctness",
                evaluator_name=RUBRIC_EVALUATOR_NAME if rubric else EVALUATOR_NAME,
                evaluator_version=evaluator_version,
                initialization_parameters={
                    "deployment_name": judge_model, "evaluation_level": "turn",
                    **({} if rubric else {"threshold": 0.5}),
                },
                data_mapping=(
                    {"query": "{{item.rubric_query}}", "response": "{{item.response}}"}
                    if rubric else {field: "{{item." + field + "}}" for field in FIELDS}
                ),
            ),
        ],
    }


def create_agent_run(
    project: AIProjectClient, client: OpenAI, endpoint: str,
    dataset_path: Path, agent_name: str, agent_version: str, judge_model: str,
    evaluator_version: str | None = None,
    *, output_dir: Path, evaluator_kind: str = "custom", comparison_id: str | None = None,
    run_label: str | None = None,
) -> dict:
    cases, reference_sha = load_dataset(dataset_path)
    agent = project.agents.get_version(agent_name=agent_name, agent_version=agent_version)
    agent_ref = {"name": agent.name, "version": agent.version}
    rows = []
    sources = []
    for case in cases:
        path = response_path(output_dir / "responses", agent.name, agent.version, case["id"])
        captured = capture_case(
            project, endpoint, agent.name, agent.version, case, reference_sha, path,
            human_review=False,
        )
        rows.append({
            **{key: case[key] for key in ("id", "query", "context", "ground_truth")},
            **{key: json.dumps(case[key], ensure_ascii=False)
               for key in ("criteria", "critical_errors")},
            "response": captured["answer"], "case_id": case["id"], "agent": agent_ref,
            "source_response_id": captured["response"]["id"],
        })
        sources.append({
            "path": str(path.resolve()), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "case_id": case["id"], "response_id": captured["response"]["id"],
        })
    record = create_regression_run(
        project, client, endpoint, rows,
        {
            "mode": "live", "source": str(dataset_path.resolve()),
            "reference_sha256": reference_sha, "sources": sources,
            "note": "Direct Foundry agent responses followed by full-rubric Foundry Evaluation. "
                    "Only query is sent to the agent; reference evidence and criteria go to the judge.",
        },
        judge_model, evaluator_version, evaluator_kind=evaluator_kind,
        comparison_id=comparison_id, run_label=run_label,
    )
    record["agent"] = agent_ref
    return record


def create_regression_run(
    project: AIProjectClient, client: OpenAI, endpoint: str,
    rows: list[dict], provenance: dict, judge_model: str,
    evaluator_version: str | None = None,
    *, evaluator_kind: str = "custom", comparison_id: str | None = None,
    run_label: str | None = None,
) -> dict:
    rubric = evaluator_kind == "rubric"
    name = RUBRIC_EVALUATOR_NAME if rubric else EVALUATOR_NAME
    definition = rubric_definition() if rubric else evaluator_definition()
    definition_sha = hashlib.sha256(json.dumps(definition, sort_keys=True).encode()).hexdigest()
    if evaluator_version:
        evaluator = project.evaluators.get_version(name=name, version=evaluator_version)
    else:
        evaluator = project.evaluators.create_version(
            name=name,
            evaluator_version={
                "name": name,
                "display_name": "Project planning business rubric" if rubric else "Project planning business correctness",
                "description": ("Equal-weight Foundry rubric trial against the existing business references."
                                if rubric else "Binary mandatory-fact and critical-error evaluation, including escalation deadlines."),
                **({} if rubric else {"evaluator_type": "custom"}),
                "categories": ["quality"],
                "supported_evaluation_levels": ["turn"],
                "metadata": {"definition_sha256": definition_sha},
                "definition": definition,
            },
            headers={"Foundry-Features": "Evaluations=V1Preview"},
        )
    saved = project.evaluators.get_version(name=name, version=evaluator.version)
    persisted = saved.definition.as_dict()
    matches = (
        all(persisted.get(key) == value for key, value in definition.items())
        if rubric else persisted == definition
    )
    if not matches:
        raise RuntimeError("Persisted Foundry evaluator definition differs from requested definition")
    print(f"Foundry evaluator: {evaluator.name} version {evaluator.version}", flush=True)
    body = evaluation_body(judge_model, evaluator.version, evaluator_kind=evaluator_kind)
    comparison = provenance.get("mode") in ("comparison", "live")
    case_ids = sorted({row.get("case_id", row["id"]) for row in rows})
    scope = case_ids[0] if len(case_ids) == 1 else f"{case_ids[0]}..{case_ids[-1]}"
    reference_sha = provenance.get("reference_sha256", "")
    agents = sorted({(row["agent"]["name"], row["agent"]["version"]) for row in rows if "agent" in row})
    agent_label = " vs ".join(f"{name} v{version}" for name, version in agents) or "Calibration"
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
    submitted = [{key: row[key] for key in ("id", *FIELDS)} for row in rows]
    if rubric:
        for item in submitted:
            item["rubric_query"] = json.dumps({
                "original_question": item["query"], "reference_context": item["context"],
                "expected_answer": item["ground_truth"],
                "mandatory_criteria": json.loads(item["criteria"]),
                "critical_errors": json.loads(item["critical_errors"]),
            }, ensure_ascii=False)
    # Identity stays in run metadata, never in the blinded judge's input.
    comparison_id = comparison_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    input_asset = register_evaluation_data(
        project, submitted, agent_names=[name for name, _ in agents],
    )
    body = {
        "name": f"{display_label} | {comparison_id}",
        "data_source": {
            "type": "jsonl",
            "source": {"type": "file_id", "id": input_asset["id"]},
        },
        "metadata": {
            "comparison_id": comparison_id, "agents": agent_label,
            "reference_sha256": reference_sha, "input_version": input_asset["version"],
        },
    }
    run = client.post(
        f"/evals/{evaluation['id']}/runs", cast_to=RunCreateResponse,
        body=body,
    )
    if not isinstance(run, RunCreateResponse):
        raise TypeError("Unexpected evaluation run creation response")
    record = {
        "schema_version": 1,
        "project_endpoint": endpoint,
        "dataset": {
            "case_count": len(rows), "cases": rows, **provenance,
            "registered_input": input_asset,
        },
        "comparison_id": comparison_id,
        "evaluator": {
            "kind": evaluator_kind, "name": evaluator.name, "version": evaluator.version,
            "judge_model": judge_model, "definition_sha256": definition_sha, "definition": persisted,
        },
        "evaluation_id": evaluation["id"], "run_id": run.id,
        "evaluation": evaluation,
        "run": run.model_dump(mode="json"),
        "output_items": [],
    }
    return record
