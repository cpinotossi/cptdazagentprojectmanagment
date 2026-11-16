"""Register test data and run Foundry Groundedness against the current agent."""

import argparse
import hashlib
import json
import os
import time
from pathlib import Path

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import TestingCriterionAzureAIEvaluator
from azure.identity import AzureCliCredential
from openai import OpenAI
from openai.types import EvalCreateResponse
from openai.types.evals import RunCreateResponse, RunRetrieveResponse

from setup_agent import AGENT_NAME
from foundry_datasets import REFERENCE_NAME, REFERENCE_PATH

ROOT = Path(__file__).resolve().parents[1]
DATASET_NAME = REFERENCE_NAME
EVALUATOR = "builtin.groundedness"
THRESHOLD = 3


def load_cases(path: Path) -> list[dict[str, str]]:
    cases = []
    seen = set()
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        row = json.loads(line)
        if not isinstance(row, dict):
            raise ValueError(f"Dataset line {number} must be a JSON object")
        for field in ("id", "query", "context", "ground_truth"):
            if not isinstance(row.get(field), str) or not row[field].strip():
                raise ValueError(f"Dataset line {number}: missing or empty {field}")
        if "response" in row:
            raise ValueError(f"Dataset line {number}: agent-target input must not contain response")
        if row["id"] in seen:
            raise ValueError(f"Dataset line {number}: duplicate id {row['id']}")
        seen.add(row["id"])
        cases.append({field: row[field] for field in ("id", "query", "context", "ground_truth")})
    if not cases:
        raise ValueError("Dataset must contain at least one test case")
    return cases


def evaluation_body(judge_model: str, evaluator_version: str) -> dict:
    criterion = TestingCriterionAzureAIEvaluator(
        type="azure_ai_evaluator",
        name="Groundedness",
        evaluator_name=EVALUATOR,
        evaluator_version=evaluator_version,
        initialization_parameters={
            "deployment_name": judge_model, "threshold": THRESHOLD, "evaluation_level": "turn",
        },
        data_mapping={
            "query": "{{item.query}}",
            "response": "{{sample.output_text}}",
            "context": "{{item.context}}",
        },
    )
    return {
        "name": "Project planning agent - Groundedness",
        "data_source_config": {
            "type": "custom",
            "item_schema": {
                "type": "object",
                "properties": {
                    field: {"type": "string"} for field in ("id", "query", "context", "ground_truth")
                },
                "required": ["id", "query", "context"],
            },
            "include_sample_schema": True,
        },
        "testing_criteria": [criterion],
    }


def run_body(dataset_id: str, agent_name: str, agent_version: str) -> dict:
    return {
        "name": f"{agent_name}-v{agent_version}-groundedness",
        "data_source": {
            "type": "azure_ai_target_completions",
            "source": {"type": "file_id", "id": dataset_id},
            "target": {"type": "azure_ai_agent", "name": agent_name, "version": agent_version},
            "input_messages": {
                "type": "template",
                "template": [{
                    "type": "message",
                    "role": "user",
                    "content": {"type": "input_text", "text": "{{item.query}}"},
                }],
            },
        },
        "metadata": {"agent_name": agent_name, "agent_version": agent_version},
    }


def save_result(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def create_run(
    project: AIProjectClient, client: OpenAI, endpoint: str, dataset_path: Path,
    agent_name: str, agent_version: str | None, judge_model: str,
) -> dict:
    cases = load_cases(dataset_path)
    digest = hashlib.sha256(dataset_path.read_bytes()).hexdigest()
    if agent_version:
        agent = project.agents.get_version(agent_name=agent_name, agent_version=agent_version)
    else:
        agent = next(iter(project.agents.list_versions(
            agent_name=agent_name, limit=1, order="desc", include_drafts=False,
        )), None)
    if agent is None:
        raise ValueError(f"No published version found for agent {agent_name}")
    evaluator = project.evaluators.get_version(name=EVALUATOR, version="latest")
    if not evaluator.version:
        raise ValueError("Groundedness evaluator did not return a version")

    from business_evaluation_datasets import register_file

    dataset = register_file(project, DATASET_NAME, dataset_path)

    # Azure evaluator and agent-target schemas extend the standard OpenAI request types.
    evaluation = client.post(
        "/evals", cast_to=EvalCreateResponse,
        body=evaluation_body(judge_model, evaluator.version),
    )
    if not isinstance(evaluation, EvalCreateResponse):
        raise TypeError("Unexpected evaluation creation response")
    print(f"Evaluation: {evaluation.id}", flush=True)
    run = client.post(
        f"/evals/{evaluation.id}/runs", cast_to=RunCreateResponse,
        body=run_body(dataset["id"], agent.name, agent.version),
    )
    if not isinstance(run, RunCreateResponse):
        raise TypeError("Unexpected evaluation run creation response")
    print(f"Run: {run.id}, agent {agent.name} version {agent.version}", flush=True)
    return {
        "schema_version": 1,
        "project_endpoint": endpoint,
        "agent": {"name": agent.name, "version": agent.version},
        "dataset": {
            "id": dataset["id"], "name": dataset["name"], "version": dataset["version"],
            "source": str(dataset_path.resolve()), "sha256": digest, "case_count": len(cases),
        },
        "evaluator": {
            "name": EVALUATOR, "version": evaluator.version,
            "judge_model": judge_model, "threshold": THRESHOLD,
            "context_source": "curated reference documents, CSV data and ontology rules",
        },
        "evaluation_id": evaluation.id,
        "run_id": run.id,
        "evaluation": evaluation.model_dump(mode="json"),
        "run": run.model_dump(mode="json"),
        "output_items": [],
    }


def wait_for_run(client: OpenAI, record: dict, result_path: Path, timeout: int) -> RunRetrieveResponse:
    deadline = time.monotonic() + timeout
    previous_status = None
    while True:
        run = client.evals.runs.retrieve(record["run_id"], eval_id=record["evaluation_id"])
        record["run"] = run.model_dump(mode="json")
        save_result(result_path, record)
        if run.status != previous_status:
            print(f"Status: {run.status}", flush=True)
            previous_status = run.status
        if run.status in ("completed", "failed", "canceled"):
            return run
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(
                f"Evaluation is still {run.status}. Resume with --resume {result_path}"
            )
        time.sleep(min(15, remaining))


def finish_run(client: OpenAI, record: dict, run: RunRetrieveResponse, result_path: Path) -> None:
    items = list(client.evals.runs.output_items.list(
        record["run_id"], eval_id=record["evaluation_id"], limit=100,
    ))
    record["output_items"] = [item.model_dump(mode="json") for item in items]
    record["summary"] = {
        "status": run.status,
        "result_counts": run.result_counts.model_dump(mode="json"),
        "report_url": run.report_url,
        "output_item_count": len(items),
    }
    save_result(result_path, record)
    print(json.dumps(record["summary"], indent=2), flush=True)
    print(f"Results saved to {result_path}", flush=True)
    if run.status != "completed":
        raise RuntimeError(f"Evaluation run {run.id} ended with {run.status}: {run.error}")
    if run.result_counts.errored or len(items) != record["dataset"]["case_count"]:
        raise RuntimeError("Evaluation has errored or missing cases; inspect the saved output_items")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default=os.environ.get("FOUNDRY_PROJECT_ENDPOINT"))
    parser.add_argument("--agent", default=AGENT_NAME)
    parser.add_argument("--agent-version")
    parser.add_argument("--judge-model", default="gpt-4.1")
    parser.add_argument("--dataset", type=Path, default=REFERENCE_PATH)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "evaluations/runs")
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--resume", type=Path)
    args = parser.parse_args()
    if not args.endpoint:
        parser.error("--endpoint or FOUNDRY_PROJECT_ENDPOINT is required")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")

    with AIProjectClient(endpoint=args.endpoint, credential=AzureCliCredential()) as project:
        with project.get_openai_client() as client:
            if args.resume:
                record = json.loads(args.resume.read_text(encoding="utf-8"))
                if record["project_endpoint"] != args.endpoint:
                    raise ValueError("Resume file belongs to a different Foundry project")
                result_path = args.resume
            else:
                record = create_run(
                    project, client, args.endpoint, args.dataset, args.agent,
                    args.agent_version, args.judge_model,
                )
                result_path = args.output_dir / f"{record['run_id']}.json"
                save_result(result_path, record)
            print(f"Result file: {result_path}", flush=True)
            run = wait_for_run(client, record, result_path, args.timeout)
            finish_run(client, record, run, result_path)


if __name__ == "__main__":
    main()
