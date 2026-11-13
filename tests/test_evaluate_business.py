import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from openai.types.evals import RunCreateResponse
from azure.core.exceptions import ResourceNotFoundError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import business_evaluation_runtime as business
import evaluate_business as cli
import business_evaluation_datasets as datasets
from business_evaluation_data import captured_cases, comparison_cases, recorded_answer, regression_cases
from business_evaluation_results import comparison_summary, verify_regression


def dataset_project(project=None):
    project = project if project is not None else Mock()
    project.datasets.get.side_effect = ResourceNotFoundError
    project.submitted = []

    def upload(**kwargs):
        project.submitted = [
            json.loads(line) for line in Path(kwargs["file_path"]).read_text().splitlines()
        ]
        return SimpleNamespace(id="dataset-id", name=kwargs["name"], version=kwargs["version"])

    project.datasets.upload_file.side_effect = upload
    return project


class BusinessEvaluationTests(unittest.TestCase):
    def evaluation_client(self):
        client = Mock()
        client.evals.list.return_value = []
        client.post.side_effect = [
            {"id": "eval-id"},
            RunCreateResponse.model_construct(id="run-id"),
        ]
        return client

    def setUp(self) -> None:
        self.source = ROOT / "evaluations/runs/evalrun_561e13fcca05413ebdec5422a4cba9ab.json"
        self.controls = ROOT / "evaluations/business_judge_controls.json"
        self.dataset = ROOT / "evaluations/archive/business_tests_v2.json"
        self.rows, self.provenance = regression_cases(
            self.dataset, self.source, self.controls,
        )

    def test_registration_reads_workspace_json_instead_of_a_python_definition(self) -> None:
        definition = json.loads(business.EVALUATOR_PATH.read_text(encoding="utf-8"))
        definition["prompt_text"] += "\nAdditional workspace-only instruction."
        project = dataset_project()
        project.evaluators.create_version.return_value = SimpleNamespace(
            name=business.EVALUATOR_NAME, version="new",
        )
        project.evaluators.get_version.return_value.definition.as_dict.return_value = definition
        client = self.evaluation_client()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evaluator.json"
            path.write_text(json.dumps(definition), encoding="utf-8")
            with patch.object(business, "EVALUATOR_PATH", path):
                record = business.create_regression_run(
                    project, client, "endpoint", self.rows, self.provenance, "judge",
                )
        registered = project.evaluators.create_version.call_args.kwargs["evaluator_version"]
        self.assertEqual(registered["definition"], definition)
        digest = hashlib.sha256(json.dumps(definition, sort_keys=True).encode()).hexdigest()
        self.assertEqual(registered["metadata"]["definition_sha256"], digest)
        self.assertEqual(record["evaluator"]["definition_sha256"], digest)

    def test_missing_or_invalid_definition_fails_before_cloud_registration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evaluator.json"
            for content, error in ((None, FileNotFoundError), ("{invalid", json.JSONDecodeError)):
                with self.subTest(content=content):
                    if content is not None:
                        path.write_text(content, encoding="utf-8")
                    project, client = Mock(), Mock()
                    with patch.object(business, "EVALUATOR_PATH", path):
                        with self.assertRaises(error):
                            business.create_regression_run(
                                project, client, "endpoint", self.rows, self.provenance, "judge",
                            )
                    project.evaluators.create_version.assert_not_called()
                    client.post.assert_not_called()

    def test_regression_replays_actual_failure_and_rejects_changed_references(self) -> None:
        self.assertEqual([row["expected_pass"] for row in self.rows], [False, True, False, False])
        source = json.loads(self.source.read_text())
        item = next(item for item in source["output_items"] if item["datasource_item"]["id"] == "B-001")
        self.assertEqual(self.rows[0]["response"], recorded_answer(item))
        self.assertIn("vier Wochen", self.rows[0]["response"])
        self.assertEqual(self.rows[0]["origin"], "recorded_agent")
        self.assertEqual(self.provenance["source_agent"]["version"], "2")
        changed = json.loads(self.dataset.read_text())
        changed["test_cases"][0]["context"] = "Changed evidence"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "changed.json"
            path.write_text(json.dumps(changed))
            with self.assertRaisesRegex(ValueError, "differs from current reference"):
                regression_cases(path, self.source, self.controls)

    def test_historical_answers_cannot_use_the_consolidated_policy_reference(self) -> None:
        active = ROOT / "evaluations/business_tests.json"
        other = ROOT / "evaluations/runs/evalrun_37a9833521b0411a866a1fb88d56f950.json"
        with self.assertRaisesRegex(ValueError, "differs from current reference"):
            regression_cases(active, self.source, self.controls)
        with self.assertRaisesRegex(ValueError, "exactly one response for every reference case"):
            comparison_cases(active, [self.source, other])

    def test_registration_and_run_send_full_rubric_but_no_expected_labels_to_judge(self) -> None:
        project = dataset_project()
        project.evaluators.create_version.return_value = SimpleNamespace(
            name=business.EVALUATOR_NAME, version="1",
        )
        project.evaluators.get_version.return_value.definition.as_dict.return_value = (
            business.evaluator_definition()
        )
        client = self.evaluation_client()
        record = business.create_regression_run(
            project, client, "https://foundry.example", self.rows, self.provenance, "judge",
        )
        registered = project.evaluators.create_version.call_args.kwargs["evaluator_version"]
        self.assertEqual(
            project.evaluators.create_version.call_args.kwargs["headers"],
            {"Foundry-Features": "Evaluations=V1Preview"},
        )
        self.assertEqual(registered["definition"]["metrics"]["business_correctness"]["type"], "boolean")
        criterion = client.post.call_args_list[0].kwargs["body"]["testing_criteria"][0]
        self.assertEqual(criterion["data_mapping"], {
            field: "{{item." + field + "}}" for field in business.FIELDS
        })
        run_body = client.post.call_args_list[1].kwargs["body"]
        self.assertEqual(run_body["data_source"]["type"], "jsonl")
        self.assertEqual(run_body["data_source"]["source"], {"type": "file_id", "id": "dataset-id"})
        for row in self.rows:
            exported = next(item for item in project.submitted if item["id"] == row["id"])
            self.assertEqual(exported, {key: row[key] for key in ("id", *business.FIELDS)})
            self.assertNotIn("expected_pass", exported)
            self.assertNotIn("origin", exported)
        self.assertEqual(record["dataset"]["cases"], self.rows)
        project.agents.create_version.assert_not_called()
        project.get_openai_client.assert_not_called()
        self.assertNotIn("target", run_body["data_source"])

    def test_persisted_prompt_mismatch_stops_before_any_evaluation_is_created(self) -> None:
        project = dataset_project()
        project.evaluators.create_version.return_value = SimpleNamespace(
            name=business.EVALUATOR_NAME, version="1",
        )
        definition = business.evaluator_definition()
        definition["prompt_text"] = "Unexpected changed rubric"
        project.evaluators.get_version.return_value.definition.as_dict.return_value = definition
        client = Mock()
        with self.assertRaisesRegex(RuntimeError, "differs from requested definition"):
            business.create_regression_run(
                project, client, "https://foundry.example", self.rows, self.provenance, "judge",
            )
        client.post.assert_not_called()

    def test_comparison_replays_both_agents_and_reuses_calibrated_evaluator(self) -> None:
        other = ROOT / "evaluations/runs/evalrun_37a9833521b0411a866a1fb88d56f950.json"
        rows, provenance = comparison_cases(self.dataset, [self.source, other])
        self.assertEqual(len(rows), 10)
        self.assertEqual(len({row["id"] for row in rows}), 10)
        self.assertEqual({row["case_id"] for row in rows}, {f"B-00{i}" for i in range(1, 6)})
        self.assertTrue(all("expected_pass" not in row for row in rows))
        for path in (self.source, other):
            source = json.loads(path.read_text())
            for item in source["output_items"]:
                row = next(
                    row for row in rows
                    if row["agent"] == source["agent"] and row["case_id"] == item["datasource_item"]["id"]
                )
                self.assertEqual(row["response"], recorded_answer(item))
        project = dataset_project()
        evaluator = project.evaluators.get_version.return_value
        evaluator.name = business.EVALUATOR_NAME
        evaluator.version = "2"
        evaluator.definition.as_dict.return_value = business.evaluator_definition()
        client = self.evaluation_client()
        record = business.create_regression_run(
            project, client, "endpoint", rows, provenance, "judge", "2",
        )
        project.evaluators.create_version.assert_not_called()
        self.assertEqual(record["evaluator"]["version"], "2")
        for exported in project.submitted:
            self.assertNotIn("agent", exported)
            self.assertNotIn("source_run_id", exported)
        record["output_items"] = [
            {"status": "completed", "datasource_item": {"id": row["id"]},
             "results": [{"status": "completed", "passed": row["id"].startswith("A2-"), "reason": "Evidence"}]}
            for row in rows
        ]
        summary = comparison_summary(record)
        self.assertEqual(summary["agents"]["project-planning-baseline:v2"]["failed"], 5)
        self.assertEqual(summary["agents"]["project-planning-agent:v7"]["passed"], 5)
        record["output_items"].pop()
        with self.assertRaisesRegex(ValueError, "missing"):
            comparison_summary(record)

    def test_comparison_rejects_duplicate_agents_and_mismatched_source_data(self) -> None:
        with self.assertRaisesRegex(ValueError, "distinct"):
            comparison_cases(self.dataset, [self.source, self.source])
        source = json.loads(self.source.read_text())
        source["agent"] = {"name": "different-agent", "version": "1"}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.json"
            source["dataset"]["sha256"] = "changed"
            path.write_text(json.dumps(source))
            with self.assertRaisesRegex(ValueError, "different datasets"):
                comparison_cases(self.dataset, [self.source, path])
            source["dataset"] = json.loads(self.source.read_text())["dataset"]
            source["output_items"][0]["datasource_item"]["query"] = "changed question"
            path.write_text(json.dumps(source))
            with self.assertRaisesRegex(ValueError, "differs from current"):
                comparison_cases(self.dataset, [self.source, path])

    def test_runs_reuse_their_evaluation_but_keep_agents_and_run_ids_separate(self) -> None:
        project = dataset_project()
        evaluator = project.evaluators.get_version.return_value
        evaluator.name, evaluator.version = business.EVALUATOR_NAME, "6"
        evaluator.definition.as_dict.return_value = business.evaluator_definition()
        row = self.rows[0] | {"agent": {"name": "iq-agent", "version": "7"}, "case_id": "B-001"}
        provenance = {"mode": "comparison", "reference_sha256": "a" * 64}
        client = self.evaluation_client()
        first = business.create_regression_run(
            project, client, "endpoint", [row], provenance, "judge", "6",
            comparison_id="pair-1", run_label="Fabric IQ",
        )
        definition = client.post.call_args_list[0].kwargs["body"]
        client.evals.list.return_value = [
            SimpleNamespace(id="eval-id", metadata=definition["metadata"]),
        ]
        client.evals.retrieve.return_value.model_dump.return_value = {"id": "eval-id", **definition}
        client.post.side_effect = [RunCreateResponse.model_construct(id="second-run")]
        second = business.create_regression_run(
            project, client, "endpoint", [row], provenance, "judge", "6",
            comparison_id="pair-2", run_label="Fabric IQ",
        )
        self.assertEqual(client.post.call_count, 3)
        self.assertEqual(first["evaluation_id"], second["evaluation_id"])
        self.assertNotEqual(first["run_id"], second["run_id"])
        other = row | {"agent": {"name": "basic-agent", "version": "4"}}
        client.post.side_effect = [
            {"id": "basic-eval"}, RunCreateResponse.model_construct(id="basic-run"),
        ]
        basic = business.create_regression_run(
            project, client, "endpoint", [other], provenance, "judge", "6",
            comparison_id="pair-2", run_label="Basic",
        )
        self.assertNotEqual(second["evaluation_id"], basic["evaluation_id"])
        self.assertNotEqual(second["run_id"], basic["run_id"])
        self.assertEqual(second["comparison_id"], basic["comparison_id"])
        self.assertIn("Fabric IQ | iq-agent v7", definition["name"])
        self.assertIn("Basic | basic-agent v4", client.post.call_args_list[-2].kwargs["body"]["name"])
        self.assertEqual({item["agent"]["name"] for item in basic["dataset"]["cases"]}, {"basic-agent"})
        self.assertTrue(all(set(item) == {"id", *business.FIELDS} for item in project.submitted))

    def test_live_agent_evaluation_sends_only_query_and_judges_with_full_rubric(self) -> None:
        project = dataset_project(MagicMock())
        project.agents.get_version.return_value = SimpleNamespace(name="agent", version="7")
        agent_client = project.get_openai_client.return_value.__enter__.return_value
        raw_response = agent_client.responses.with_raw_response.create.return_value
        raw_response.parse.return_value.status = "completed"
        raw_response.parse.return_value.output_text = "Actual generated answer"
        raw_response.http_response.json.side_effect = lambda: {
            "id": "response-id", "tools": [{"type": "fabric_iq_preview"}],
            "output": [{
                "type": "mcp_call", "name": "ask_ontology",
                "arguments": json.dumps({
                    "request": agent_client.responses.with_raw_response.create.call_args.kwargs["input"],
                }),
            }],
        }
        evaluator = project.evaluators.get_version.return_value
        evaluator.name = business.EVALUATOR_NAME
        evaluator.version = "2"
        evaluator.definition.as_dict.return_value = business.evaluator_definition()
        client = self.evaluation_client()
        dataset = ROOT / "evaluations/business_tests.json"
        cases = json.loads(dataset.read_text())["test_cases"]
        with tempfile.TemporaryDirectory() as directory:
            record = business.create_agent_run(
                project, client, "endpoint", dataset, "agent", "7", "judge", "2",
                output_dir=Path(directory),
            )
            sources = record["dataset"]["sources"]
            self.assertEqual(len(sources), len(cases))
            for source in sources:
                saved = json.loads(Path(source["path"]).read_text())
                self.assertEqual(saved["status"], "captured")
                self.assertNotIn("review", saved)
                self.assertEqual(saved["response"]["tools"][0]["type"], "fabric_iq_preview")
        calls = agent_client.responses.with_raw_response.create.call_args_list
        self.assertEqual(len(calls), len(cases))
        for call, case in zip(calls, cases):
            self.assertEqual(call.kwargs, {
                "input": case["query"],
                "extra_body": {"agent_reference": {
                    "type": "agent_reference", "name": "agent", "version": "7",
                }},
            })
        evaluation = client.post.call_args_list[0].kwargs["body"]
        self.assertEqual(evaluation["testing_criteria"][0]["data_mapping"], {
            field: "{{item." + field + "}}" for field in business.FIELDS
        })
        run = client.post.call_args_list[1].kwargs["body"]
        source = run["data_source"]
        self.assertEqual(source["type"], "jsonl")
        submitted = project.submitted
        self.assertEqual(len(submitted), len(cases))
        for row, case in zip(submitted, cases):
            self.assertEqual(set(row), {"id", *business.FIELDS})
            self.assertEqual(row["response"], "Actual generated answer")
            self.assertEqual(row["context"], case["context"])
            self.assertEqual(json.loads(row["criteria"]), case["criteria"])
            self.assertEqual(json.loads(row["critical_errors"]), case["critical_errors"])
        project.agents.get_version.assert_called_once_with(agent_name="agent", agent_version="7")
        project.agents.create_version.assert_not_called()
        project.evaluators.create_version.assert_not_called()
        self.assertEqual(record["agent"], {"name": "agent", "version": "7"})
        self.assertEqual(record["dataset"]["mode"], "live")
        record["output_items"] = [
            {"status": "completed", "datasource_item": {"id": row["id"]},
             "results": [{"status": "completed", "passed": True, "reason": "Cloud judgment"}]}
            for row in submitted
        ]
        summary = comparison_summary(record)
        self.assertEqual(summary["agents"]["agent:v7"]["passed"], len(cases))
        self.assertEqual(summary["agents"]["agent:v7"]["cases"]["B-001"]["reason"], "Cloud judgment")
        record["output_items"].pop()
        with self.assertRaisesRegex(ValueError, "missing"):
            comparison_summary(record)

    def test_failed_live_capture_stops_before_foundry_grading(self) -> None:
        project = dataset_project()
        project.agents.get_version.return_value = SimpleNamespace(name="agent", version="7")
        client = Mock()
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(business, "capture_case", side_effect=RuntimeError("Agent tool failed")):
                with self.assertRaisesRegex(RuntimeError, "Agent tool failed"):
                    business.create_agent_run(
                        project, client, "endpoint", ROOT / "evaluations/business_tests.json",
                        "agent", "7", "judge", "2", output_dir=Path(directory),
                    )
        client.post.assert_not_called()

    def test_comparison_replays_live_capture_answers_without_requiring_new_completions(self) -> None:
        dataset = ROOT / "evaluations/business_tests.json"
        cases = json.loads(dataset.read_text())["test_cases"]
        digest = hashlib.sha256(dataset.read_bytes()).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for number in (1, 2):
                source = {
                    "agent": {"name": f"agent-{number}", "version": "1"},
                    "run_id": f"source-{number}",
                    "dataset": {"mode": "live", "reference_sha256": digest},
                    "output_items": [
                        {"status": "completed", "datasource_item": {
                            **{key: case[key] for key in ("id", "query", "context", "ground_truth")},
                            "response": f"Saved answer {number} {case['id']}",
                        }}
                        for case in cases
                    ],
                }
                path = Path(directory) / f"source-{number}.json"
                path.write_text(json.dumps(source))
                paths.append(path)
            rows, provenance = comparison_cases(dataset, paths)
            self.assertEqual(len(rows), 2 * len(cases))
            self.assertEqual(rows[0]["response"], "Saved answer 1 B-001")
            self.assertEqual(rows[len(cases)]["response"], "Saved answer 2 B-001")
            self.assertEqual(provenance["source_dataset_sha256"], digest)
            source["output_items"][0]["datasource_item"]["response"] = ""
            paths[1].write_text(json.dumps(source))
            with self.assertRaisesRegex(ValueError, "source response missing"):
                comparison_cases(dataset, paths)

    def test_rubric_registers_service_dimensions_and_maps_references_only_to_judge(self) -> None:
        project = dataset_project()
        project.evaluators.create_version.return_value = SimpleNamespace(
            name=business.RUBRIC_EVALUATOR_NAME, version="1",
        )
        definition = business.rubric_definition()
        project.evaluators.get_version.return_value.definition.as_dict.return_value = definition
        client = self.evaluation_client()
        row = self.rows[0] | {"agent": {"name": "agent", "version": "7"}, "case_id": "B-001"}
        record = business.create_regression_run(
            project, client, "endpoint", [row], {"mode": "comparison"}, "judge",
            evaluator_kind="rubric",
        )
        registered = project.evaluators.create_version.call_args.kwargs
        self.assertEqual(registered["name"], business.RUBRIC_EVALUATOR_NAME)
        self.assertEqual(registered["evaluator_version"]["definition"]["type"], "rubric")
        self.assertNotIn("prompt_text", registered["evaluator_version"]["definition"])
        self.assertEqual(definition["pass_threshold"], 0.5)
        self.assertTrue(all(dim["weight"] == 1 for dim in definition["dimensions"]))
        criterion = client.post.call_args_list[0].kwargs["body"]["testing_criteria"][0]
        self.assertEqual(criterion["evaluator_name"], business.RUBRIC_EVALUATOR_NAME)
        self.assertEqual(criterion["data_mapping"], {
            "query": "{{item.rubric_query}}", "response": "{{item.response}}",
        })
        submitted = project.submitted[0]
        packet = json.loads(submitted["rubric_query"])
        self.assertEqual(submitted["response"], row["response"])
        self.assertEqual(submitted["query"], row["query"])
        self.assertEqual(packet["reference_context"], row["context"])
        self.assertEqual(packet["expected_answer"], row["ground_truth"])
        self.assertEqual(packet["mandatory_criteria"], json.loads(row["criteria"]))
        self.assertNotIn("agent", submitted)
        self.assertNotIn("expected_pass", submitted)
        project.get_openai_client.assert_not_called()
        record["output_items"] = [{
            "status": "completed", "datasource_item": {"id": row["id"]},
            "results": [{
                "status": "completed", "passed": True, "score": 0.75,
                "reason": "Cloud rubric decision",
                "properties": {"dimension_scores": [{"id": "shared_data", "score": 4}]},
            }],
        }]
        result = comparison_summary(record)["agents"]["agent:v7"]["cases"]["B-001"]
        self.assertEqual(result["score"], 0.75)
        self.assertEqual(result["properties"]["dimension_scores"][0]["score"], 4)
        record["output_items"][0]["results"][0]["score"] = None
        with self.assertRaisesRegex(ValueError, "missing or invalid normalized rubric score"):
            comparison_summary(record)

    def test_regression_verification_distinguishes_expected_rejection_from_execution_errors(self) -> None:
        record = {
            "dataset": {"cases": self.rows},
            "output_items": [
                {
                    "status": "completed",
                    "datasource_item": {"id": row["id"]},
                    "results": [{
                        "status": "completed", "passed": row["expected_pass"], "reason": "Evidence",
                    }],
                }
                for row in self.rows
            ],
        }
        self.assertTrue(verify_regression(record)["passed"])
        changed = copy.deepcopy(record)
        changed["output_items"][0]["results"][0]["passed"] = True
        self.assertFalse(verify_regression(changed)["passed"])
        for mutation in ("missing", "duplicate", "error", "nonboolean"):
            with self.subTest(mutation=mutation):
                changed = copy.deepcopy(record)
                if mutation == "missing":
                    changed["output_items"].pop()
                elif mutation == "duplicate":
                    changed["output_items"].append(changed["output_items"][0])
                elif mutation == "error":
                    changed["output_items"][0]["results"][0]["status"] = "error"
                else:
                    changed["output_items"][0]["results"][0]["passed"] = "false"
                with self.assertRaises(ValueError):
                    verify_regression(changed)


class CapturedResponseTests(unittest.TestCase):
    def test_judging_rejects_a_truncated_fabric_handoff_even_with_completed_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = self.captures(directory)
            source = json.loads(paths[0].read_text())
            source["response"]["tools"] = [{"type": "fabric_iq_preview"}]
            source["response"]["output"].insert(0, {
                "type": "mcp_call", "name": "ask_ontology",
                "arguments": json.dumps({"request": "Subject without the email body"}),
            })
            paths[0].write_text(json.dumps(source))
            with self.assertRaisesRegex(ValueError, "complete original user message"):
                captured_cases(ROOT / "evaluations/business_tests.json", paths, "endpoint", "B-001")

    def batch_captures(self, directory: str) -> list[Path]:
        dataset = ROOT / "evaluations/business_tests.json"
        template = json.loads(self.captures(directory)[0].read_text())
        paths = []
        for case in json.loads(dataset.read_text())["test_cases"]:
            source = copy.deepcopy(template)
            source.update(case_id=case["id"], query=case["query"])
            source["response"]["id"] = f"response-{case['id']}"
            path = Path(directory) / f"{case['id']}.capture.json"
            path.write_text(json.dumps(source))
            paths.append(path)
        return paths

    def test_batch_judging_requires_all_cases_and_submits_only_saved_answers(self) -> None:
        dataset = ROOT / "evaluations/business_tests.json"
        with tempfile.TemporaryDirectory() as directory:
            paths = self.batch_captures(directory)
            rows, provenance = captured_cases(dataset, paths, "endpoint")
            cases = json.loads(dataset.read_text())["test_cases"]
            self.assertEqual(len(rows), len(cases))
            self.assertEqual({row["case_id"] for row in rows}, {case["id"] for case in cases})
            project = dataset_project()
            evaluator = project.evaluators.get_version.return_value
            evaluator.name, evaluator.version = business.EVALUATOR_NAME, "6"
            evaluator.definition.as_dict.return_value = business.evaluator_definition()
            client = Mock()
            client.evals.list.return_value = []
            client.post.side_effect = [
                {"id": "eval-id"}, RunCreateResponse.model_construct(id="run-id"),
            ]
            record = business.create_regression_run(
                project, client, "endpoint", rows, provenance, "judge", "6",
            )
            submitted = project.submitted
            self.assertEqual(len(submitted), len(cases))
            self.assertTrue(all(item["response"] == "Saved answer 1" for item in submitted))
            self.assertTrue(all(set(item) == {"id", *business.FIELDS} for item in submitted))
            self.assertEqual(record["dataset"]["case_count"], len(cases))
            project.agents.get_version.assert_not_called()
            project.evaluators.create_version.assert_not_called()
            with self.assertRaisesRegex(ValueError, "missing cases"):
                captured_cases(dataset, paths[:-1], "endpoint")
            with self.assertRaisesRegex(ValueError, "distinct agent/case"):
                captured_cases(dataset, paths + paths[:1], "endpoint")

    def captures(self, directory: str) -> list[Path]:
        dataset = ROOT / "evaluations/business_tests.json"
        case = json.loads(dataset.read_text())["test_cases"][0]
        paths = []
        for number in (1, 2):
            source = {
                "status": "captured", "project_endpoint": "endpoint",
                "dataset_sha256": hashlib.sha256(dataset.read_bytes()).hexdigest(),
                "case_id": case["id"], "query": case["query"],
                "agent": {"name": f"agent-{number}", "version": "7"},
                "response": {
                    "id": f"response-{number}", "status": "completed", "error": None,
                    "output": [{"role": "assistant", "content": [
                        {"type": "output_text", "text": f"Saved answer {number}"},
                    ]}],
                },
            }
            path = Path(directory) / f"capture-{number}.json"
            path.write_text(json.dumps(source))
            paths.append(path)
        return paths

    def test_judge_only_cli_submits_exactly_two_saved_answers_and_never_calls_agents(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = self.captures(directory)
            with patch.object(sys, "argv", [
                "evaluate_business.py", "--endpoint", "endpoint",
                "--captured-responses", *map(str, paths), "--case-id", "B-001",
                "--evaluator-version", "2", "--output-dir", directory,
            ]), patch.object(cli, "AzureCliCredential"), \
                    patch.object(cli, "AIProjectClient") as factory, \
                    patch.object(cli, "wait_for_run"), \
                    patch.object(cli, "finish_run"), \
                    patch.object(cli, "comparison_summary", return_value={"agents": {}}), \
                    patch.object(business, "capture_case") as capture:
                project = factory.return_value.__enter__.return_value
                dataset_project(project)
                client = project.get_openai_client.return_value.__enter__.return_value
                client.post.side_effect = [
                    {"id": "eval-id"}, RunCreateResponse.model_construct(id="run-id"),
                ]
                evaluator = project.evaluators.get_version.return_value
                evaluator.name, evaluator.version = business.EVALUATOR_NAME, "2"
                evaluator.definition.as_dict.return_value = business.evaluator_definition()
                cli.main()
                capture.assert_not_called()
                project.agents.get_version.assert_not_called()
                project.agents.create_version.assert_not_called()
                project.evaluators.create_version.assert_not_called()
                submitted = project.submitted
                self.assertEqual([row["response"] for row in submitted],
                                 ["Saved answer 1", "Saved answer 2"])
                self.assertEqual([row["id"] for row in submitted],
                                 ["A1-B-001", "A2-B-001"])
                self.assertTrue(all("agent" not in row for row in submitted))
                record = json.loads((Path(directory) / "run-id.json").read_text())
                self.assertEqual(record["dataset"]["case_count"], 2)

    def test_capture_validation_rejects_stale_failed_or_duplicate_sources(self) -> None:
        dataset = ROOT / "evaluations/business_tests.json"
        mutations = [
            ("project_endpoint", "other"), ("dataset_sha256", "stale"),
            ("case_id", "B-002"), ("query", "different"), ("status", "error"),
            ("agent", {"name": "agent-1", "version": "7"}),
        ]
        with tempfile.TemporaryDirectory() as directory:
            paths = self.captures(directory)
            original = json.loads(paths[1].read_text())
            for field, value in mutations:
                with self.subTest(field=field):
                    source = copy.deepcopy(original)
                    source[field] = value
                    paths[1].write_text(json.dumps(source))
                    with self.assertRaises(ValueError):
                        captured_cases(dataset, paths, "endpoint", "B-001")
            for status, output in (("failed", original["response"]["output"]), ("completed", [])):
                source = copy.deepcopy(original)
                source["response"].update(status=status, output=output)
                paths[1].write_text(json.dumps(source))
                with self.assertRaises(ValueError):
                    captured_cases(dataset, paths, "endpoint", "B-001")

    def test_single_capture_is_independent_of_the_other_agent(self) -> None:
        dataset = ROOT / "evaluations/business_tests.json"
        with tempfile.TemporaryDirectory() as directory:
            paths = self.captures(directory)
            paths[1].unlink()
            rows, provenance = captured_cases(dataset, paths[:1], "endpoint", "B-001")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["response"], "Saved answer 1")
            self.assertEqual(rows[0]["agent"]["name"], "agent-1")
            project = dataset_project()
            evaluator = project.evaluators.get_version.return_value
            evaluator.name, evaluator.version = business.EVALUATOR_NAME, "2"
            evaluator.definition.as_dict.return_value = business.evaluator_definition()
            client = Mock()
            client.evals.list.return_value = []
            client.post.side_effect = [
                {"id": "eval-id"}, RunCreateResponse.model_construct(id="run-id"),
            ]
            record = business.create_regression_run(
                project, client, "endpoint", rows, provenance, "judge", "2",
            )
            submitted = project.submitted
            self.assertEqual([row["response"] for row in submitted], ["Saved answer 1"])
            self.assertEqual(record["dataset"]["case_count"], 1)
            project.evaluators.create_version.assert_not_called()
            project.agents.get_version.assert_not_called()


class FoundryDatasetTests(unittest.TestCase):
    def test_identical_inputs_reuse_a_version_and_changed_answers_get_a_new_version(self) -> None:
        project = dataset_project()
        rows = [{"id": "B-001", "query": "Question", "response": "Answer"}]
        first = datasets.register_evaluation_data(project, rows, agent_names=["agent"])
        project.datasets.get.side_effect = None
        project.datasets.get.return_value = SimpleNamespace(id=first["id"])
        reused = datasets.register_evaluation_data(project, rows, agent_names=["agent"])
        self.assertEqual(first, reused)
        self.assertEqual(project.datasets.upload_file.call_count, 1)
        project.datasets.get.side_effect = ResourceNotFoundError
        changed = datasets.register_evaluation_data(
            project, [rows[0] | {"response": "New answer"}], agent_names=["agent"],
        )
        self.assertNotEqual(first["version"], changed["version"])
        self.assertEqual(project.datasets.upload_file.call_count, 2)
        self.assertEqual(project.submitted[0]["response"], "New answer")
        self.assertNotIn("agent", project.submitted[0])

    def test_pinned_cloud_reference_is_verified_before_using_its_exact_bytes(self) -> None:
        content = (ROOT / "evaluations/business_tests.json").read_bytes()
        project = Mock()
        project.datasets.get.return_value = SimpleNamespace(
            id="reference-id", data_uri="https://storage.invalid/reference.json",
        )
        project.datasets.get_credentials.return_value = SimpleNamespace(
            blob_reference=SimpleNamespace(credential=SimpleNamespace(
                sas_uri="https://storage.invalid/container?sig=example",
            )),
        )
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "reference.asset.json"
            asset = {
                "project_endpoint": "endpoint", "id": "reference-id", "name": "reference",
                "version": "version", "sha256": hashlib.sha256(content).hexdigest(),
            }
            manifest.write_text(json.dumps(asset))
            with patch.object(datasets.BlobClient, "from_blob_url") as factory:
                factory.return_value.__enter__.return_value.download_blob.return_value.readall.return_value = content
                snapshot = datasets.download_reference(project, "endpoint", manifest)
                self.assertEqual(snapshot.read_bytes(), content)
                with self.assertRaisesRegex(ValueError, "different Foundry project"):
                    datasets.download_reference(project, "other-endpoint", manifest)
                manifest.write_text(json.dumps(asset | {"sha256": "changed"}))
                with self.assertRaisesRegex(ValueError, "pinned hash"):
                    datasets.download_reference(project, "endpoint", manifest)


class NotebookComparisonTests(unittest.TestCase):
    def test_table_compares_cases_side_by_side_and_rejects_mismatched_runs(self) -> None:
        notebook = json.loads((ROOT / "notebooks/business_all_tests.ipynb").read_text())
        script = next("".join(cell["source"]) for cell in notebook["cells"]
                      if cell["cell_type"] == "code" and "".join(cell["source"]).startswith("COMPARISON="))
        with tempfile.TemporaryDirectory() as directory:
            paths = [Path(directory) / f"{name}.json" for name in ("iq", "basic")]
            reports = []
            for name, passed in (("iq", True), ("basic", False)):
                report = {
                    "comparison_id": "pair", "evaluator": {"version": "6"},
                    "dataset": {"reference_sha256": "reference", "cases": [{"case_id": "B-001"}]},
                    "comparison": {"agents": {f"{name}:v7": {
                        "passed": int(passed), "failed": int(not passed),
                        "cases": {"B-001": {"passed": passed, "reason": "Cloud reason"}},
                    }}},
                    "summary": {"report_url": f"https://foundry.example/{name}"},
                }
                reports.append(report)
            for path, report in zip(paths, reports):
                path.write_text(json.dumps(report))
            summary = Path(directory) / "comparison.json"
            environment = {
                **os.environ, "IQ_RESULT_FILE": str(paths[0]), "BASIC_RESULT_FILE": str(paths[1]),
                "SUMMARY_FILE": str(summary), "IQ_AGENT": "iq", "BASIC_AGENT": "basic",
                "IQ_VERSION": "7", "BASIC_VERSION": "7",
            }
            result = subprocess.run(["bash", "-c", script], env=environment, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertRegex(result.stdout, r"B-001\s+PASS\s+FAIL")
            header = next(line for line in result.stdout.splitlines() if line.startswith("Test"))
            row = next(line for line in result.stdout.splitlines() if line.startswith("B-001"))
            self.assertEqual(header.index("Basic"), row.index("FAIL"))
            self.assertIn("https://foundry.example/iq", result.stdout)
            self.assertIn("https://foundry.example/basic", result.stdout)
            original = summary.read_bytes()
            paths[1].write_text(json.dumps(reports[1] | {"comparison_id": "different-pair"}))
            failed = subprocess.run(["bash", "-c", script], env=environment, capture_output=True, text=True)
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn("Reports must use the same", failed.stderr)
            self.assertEqual(summary.read_bytes(), original)


class BusinessEvaluationCliTests(unittest.TestCase):
    def test_capture_only_refreshes_fixed_files_once_per_case_without_judging(self) -> None:
        dataset = ROOT / "evaluations/business_tests.json"
        cases = json.loads(dataset.read_text())["test_cases"]
        project, client = MagicMock(), MagicMock()
        project.agents.get_version.return_value = SimpleNamespace(name="agent", version="7")
        raw = client.responses.with_raw_response.create.return_value
        raw.parse.return_value.status = "completed"
        raw.parse.return_value.output_text = "Actual answer"
        raw.http_response.json.return_value = {
            "id": "response-id", "status": "completed",
            "output": [{"role": "assistant", "content": [
                {"type": "output_text", "text": "Actual answer"},
            ]}],
        }
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            for _ in range(2):
                paths = business.capture_agent_cases(
                    project, client, "endpoint", dataset, "agent", "7", output,
                )
                self.assertEqual([path.name for path in paths],
                                 [f"{case['id']}.capture.json" for case in cases])
                rows, _ = captured_cases(dataset, paths, "endpoint")
                self.assertEqual(len(rows), len(cases))
                for case, path in zip(cases, paths):
                    saved = json.loads(path.read_text())
                    self.assertNotIn("review", saved)
                    self.assertEqual(saved["response"], json.loads(
                        (output / f"{case['id']}.response.json").read_text(),
                    ))
            self.assertEqual(len(list(output.glob("*.json"))), 2 * len(cases))
        calls = client.responses.with_raw_response.create.call_args_list
        self.assertEqual(len(calls), 2 * len(cases))
        for call, case in zip(calls, cases * 2):
            self.assertEqual(call.kwargs, {
                "input": case["query"],
                "extra_body": {"agent_reference": {
                    "type": "agent_reference", "name": "agent", "version": "7",
                }},
            })
        client.post.assert_not_called()
        project.evaluators.create_version.assert_not_called()
        project.get_openai_client.assert_not_called()

    def test_capture_only_cli_returns_before_creating_any_evaluation(self) -> None:
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(sys, "argv", [
                    "evaluate_business.py", "--endpoint", "endpoint",
                    "--agent", "agent", "--agent-version", "7", "--capture-only",
                    "--output-dir", directory,
                ]), patch.object(cli, "AzureCliCredential"), \
                patch.object(cli, "AIProjectClient") as factory, \
                patch.object(cli, "capture_agent_cases", return_value=[]) as capture, \
                patch.object(cli, "create_agent_run") as evaluate, \
                patch.object(cli, "wait_for_run") as wait:
            cli.main()
            project = factory.return_value.__enter__.return_value
            client = project.get_openai_client.return_value.__enter__.return_value
            capture.assert_called_once_with(
                project, client, "endpoint", ROOT / "evaluations/business_tests.json",
                "agent", "7", Path(directory),
            )
            self.assertEqual(project.get_openai_client.call_args.kwargs["max_retries"], 0)
            client.post.assert_not_called()
            evaluate.assert_not_called()
            wait.assert_not_called()

    def test_fixed_result_file_is_updated_instead_of_creating_run_id_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fabric-iq.evaluation.json"
            for run_id in ("first-run", "second-run"):
                record = {"run_id": run_id, "project_endpoint": "endpoint", "dataset": {"mode": "live"}}
                with patch.object(sys, "argv", [
                    "evaluate_business.py", "--endpoint", "endpoint",
                    "--agent", "agent", "--agent-version", "7",
                    "--result-file", str(path), "--output-dir", directory,
                ]), patch.object(cli, "AzureCliCredential"), \
                        patch.object(cli, "AIProjectClient"), \
                        patch.object(cli, "create_agent_run", return_value=record), \
                        patch.object(cli, "wait_for_run") as wait, \
                        patch.object(cli, "finish_run"), \
                        patch.object(cli, "comparison_summary", return_value={"agents": {}}):
                    cli.main()
                    self.assertEqual(wait.call_args.args[2], path)
                self.assertEqual(json.loads(path.read_text())["run_id"], run_id)
            self.assertEqual(list(Path(directory).glob("*.json")), [path])

    def test_existing_live_command_dispatches_to_shared_runtime_and_result_helpers(self) -> None:
        for kind in ("custom", "rubric"):
            record = {
                "run_id": "run-id", "project_endpoint": "endpoint",
                "dataset": {"mode": "live"},
            }
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory, \
                    patch.object(sys, "argv", [
                        "evaluate_business.py", "--endpoint", "endpoint",
                        "--agent", "agent", "--agent-version", "7",
                        "--evaluator-kind", kind, "--output-dir", directory,
                    ]), \
                    patch.object(cli, "AzureCliCredential"), \
                    patch.object(cli, "AIProjectClient") as factory, \
                    patch.object(cli, "create_agent_run", return_value=record) as create, \
                    patch.object(cli, "wait_for_run") as wait, \
                    patch.object(cli, "finish_run") as finish, \
                    patch.object(cli, "comparison_summary", return_value={"agents": {}}) as summarize:
                cli.main()
                project = factory.return_value.__enter__.return_value
                client = project.get_openai_client.return_value.__enter__.return_value
                path = Path(directory) / "run-id.json"
                create.assert_called_once_with(
                    project, client, "endpoint", ROOT / "evaluations/business_tests.json",
                    "agent", "7", "gpt-4.1", None,
                    output_dir=Path(directory), evaluator_kind=kind,
                    comparison_id=None, run_label=None,
                )
                wait.assert_called_once_with(client, record, path, 1200)
                finish.assert_called_once_with(client, record, wait.return_value, path)
                summarize.assert_called_once_with(record)
                self.assertEqual(json.loads(path.read_text())["comparison"], {"agents": {}})

    def test_resume_keeps_existing_run_without_new_inference_or_registration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run-id.json"
            record = {
                "project_endpoint": "endpoint", "run_id": "run-id",
                "dataset": {"mode": "comparison"},
            }
            path.write_text(json.dumps(record))
            with patch.object(sys, "argv", [
                "evaluate_business.py", "--endpoint", "endpoint", "--resume", str(path),
            ]), \
                    patch.object(cli, "AzureCliCredential"), \
                    patch.object(cli, "AIProjectClient"), \
                    patch.object(cli, "create_agent_run") as live, \
                    patch.object(cli, "create_regression_run") as replay, \
                    patch.object(cli, "wait_for_run") as wait, \
                    patch.object(cli, "finish_run"), \
                    patch.object(cli, "comparison_summary", return_value={"agents": {}}):
                cli.main()
            live.assert_not_called()
            replay.assert_not_called()
            self.assertEqual(wait.call_args.args[1]["run_id"], "run-id")
            self.assertEqual(json.loads(path.read_text())["comparison"], {"agents": {}})


if __name__ == "__main__":
    unittest.main()
