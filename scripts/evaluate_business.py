"""Run Foundry business evaluations from the canonical dataset and a pinned agent."""

import argparse
import json
import os
from pathlib import Path

from azure.ai.projects import AIProjectClient
from azure.core.pipeline import PipelineRequest
from azure.identity import AzureCliCredential
from httpx2 import Request
from openai import DefaultHttpxClient

from business_evaluation_datasets import download_reference, publish_reference
from business_evaluation_results import comparison_summary, verify_regression
from business_evaluation_runtime import capture_agent_cases, create_agent_run
from evaluate_agent import finish_run, save_result, wait_for_run
from foundry_datasets import REFERENCE_NAME, REFERENCE_PATH, REFERENCE_SOURCE

ROOT = Path(__file__).resolve().parents[1]


def show_project_endpoint(request: PipelineRequest) -> None:
    print(f"HTTP {request.http_request.method} {request.http_request.url}", flush=True)


def show_openai_endpoint(request: Request) -> None:
    print(f"HTTP {request.method} {request.url}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default=os.environ.get("FOUNDRY_PROJECT_ENDPOINT"))
    parser.add_argument("--judge-model", default="gpt-4.1")
    parser.add_argument("--dataset", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--publish-reference", action="store_true", help="Register --dataset by content hash and save its pinned asset manifest to --result-file")
    parser.add_argument("--reference-asset", type=Path, help="Download and verify the pinned Foundry reference from this asset manifest instead of using local --dataset")
    parser.add_argument("--comparison-id", help="Shared label for the two agents' runs")
    parser.add_argument("--run-label", help="Display label such as 'Fabric IQ' or 'Basic'; runs remain separate")
    mode.add_argument("--agent", help="Let Foundry invoke this agent and judge it using the existing reference dataset")
    parser.add_argument("--capture-only", action="store_true", help="With --agent: save all answers to fixed files without judging; reruns overwrite them")
    parser.add_argument("--agent-version", help="Pinned published version for --agent")
    mode.add_argument("--source-run", type=Path, help="Disabled: cloud replay of saved answers")
    parser.add_argument("--controls", type=Path, help="Disabled: cloud calibration")
    mode.add_argument(
        "--compare-runs", type=Path, nargs="+",
        help="Disabled: cloud replay of saved answers",
    )
    mode.add_argument(
        "--captured-responses", type=Path, nargs="+",
        help="Disabled: cloud judging of saved captures",
    )
    parser.add_argument("--case-id", help="Single reference case for --captured-responses")
    parser.add_argument("--evaluator-version", help="Reuse a pinned version of the selected evaluator kind")
    parser.add_argument("--evaluator-kind", choices=("custom", "rubric"), default="custom")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "evaluations/runs")
    parser.add_argument("--result-file", type=Path, help="Write the run to this fixed file instead of a run-ID filename")
    parser.add_argument("--show-endpoints", action="store_true", help="Print HTTP methods and exact request URLs, without headers or bodies")
    parser.add_argument("--timeout", type=int, default=1200)
    parser.add_argument("--resume", type=Path)
    args = parser.parse_args()
    args.dataset = args.dataset or (REFERENCE_SOURCE if args.publish_reference else REFERENCE_PATH)
    if args.source_run or args.controls or args.compare_runs or args.captured_responses or args.case_id:
        parser.error(
            "Cloud judging of saved answers and calibration controls is disabled because inline "
            "inputs create additional Foundry datasets. Use --agent and --agent-version instead."
        )
    if not (args.agent or args.publish_reference or args.resume):
        parser.error("Choose --agent with --agent-version, --publish-reference or --resume")
    if args.evaluator_kind != "custom" and not args.resume:
        parser.error("Direct dataset runs support --evaluator-kind custom only; rubric replay is disabled")
    if not args.endpoint or args.timeout <= 0:
        parser.error("--endpoint (or FOUNDRY_PROJECT_ENDPOINT) and a positive timeout are required")
    if args.publish_reference and (not args.result_file or args.reference_asset or args.resume):
        parser.error("--publish-reference requires --result-file, without --reference-asset or --resume")
    if args.publish_reference and args.result_file.resolve() == args.dataset.resolve():
        parser.error("--result-file must not overwrite the reference dataset")
    if bool(args.agent) != bool(args.agent_version):
        parser.error("--agent and --agent-version must be supplied together")
    if args.capture_only and (not args.agent or args.resume or args.result_file):
        parser.error("--capture-only requires --agent and --agent-version, without --resume or --result-file")
    if args.result_file and args.resume:
        parser.error("--result-file cannot be combined with --resume; resume writes to its existing file")
    with AIProjectClient(
        endpoint=args.endpoint, credential=AzureCliCredential(),
        **({"raw_request_hook": show_project_endpoint} if args.show_endpoints else {}),
    ) as project:
        if args.publish_reference:
            asset = publish_reference(project, args.dataset)
            save_result(args.result_file, {"project_endpoint": args.endpoint, **asset})
            download_reference(project, args.endpoint, args.result_file)
            print(json.dumps(asset, indent=2))
            return
        reference_asset = None
        if args.reference_asset:
            reference_asset = json.loads(args.reference_asset.read_text(encoding="utf-8"))
            if args.agent and not args.capture_only and reference_asset["name"] != REFERENCE_NAME:
                raise ValueError("Direct agent runs require the canonical Foundry reference dataset")
            args.dataset = download_reference(project, args.endpoint, args.reference_asset)
        with project.get_openai_client(
            **({"max_retries": 0} if args.capture_only else {}),
            **({"http_client": DefaultHttpxClient(
                event_hooks={"request": [show_openai_endpoint]},
            )} if args.show_endpoints else {}),
        ) as client:
            if args.capture_only:
                paths = capture_agent_cases(
                    project, client, args.endpoint, args.dataset,
                    args.agent, args.agent_version, args.output_dir,
                )
                print(json.dumps({"captured": len(paths), "files": [str(path) for path in paths]}, indent=2))
                return
            if args.resume:
                record = json.loads(args.resume.read_text(encoding="utf-8"))
                if record["project_endpoint"] != args.endpoint:
                    raise ValueError("Resume file belongs to a different Foundry project")
                path = args.resume
            else:
                record = create_agent_run(
                    project, client, args.endpoint, args.dataset, args.agent,
                    args.agent_version, args.judge_model, args.evaluator_version,
                    evaluator_kind=args.evaluator_kind,
                    comparison_id=args.comparison_id,
                    run_label=args.run_label,
                )
                if reference_asset:
                    record["dataset"]["reference_asset"] = reference_asset
                path = args.result_file or args.output_dir / f"{record['run_id']}.json"
                save_result(path, record)
            print(f"Result file: {path}", flush=True)
            run = wait_for_run(client, record, path, args.timeout)
            finish_run(client, record, run, path)
            if record["dataset"].get("mode") in ("comparison", "live"):
                record.pop("comparison", None)
                save_result(path, record)
                record["comparison"] = comparison_summary(record)
                save_result(path, record)
                print(json.dumps(record["comparison"], indent=2, ensure_ascii=False), flush=True)
                return
            record["regression"] = verify_regression(record)
            save_result(path, record)
            print(json.dumps(record["regression"], indent=2, ensure_ascii=False), flush=True)
            if not record["regression"]["passed"]:
                raise RuntimeError("Business judge failed calibration; inspect the saved Foundry decisions")


if __name__ == "__main__":
    main()
