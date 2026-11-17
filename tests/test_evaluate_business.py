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

from azure.core.exceptions import ResourceNotFoundError
from openai.types.evals import RunCreateResponse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import business_evaluation_runtime as business
import evaluate_business as cli
import business_evaluation_datasets as datasets
from business_evaluation_data import captured_cases, comparison_cases, recorded_answer, regression_cases
from business_evaluation_results import comparison_summary, verify_regression

ENSURE_ANCHORS = business.ensure_anchors_evaluator  # the real function; setUp patches the module attribute

class DirectAgentEvaluationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dataset = ROOT / "evaluations/generated/business_reference.jsonl"
        self.cases = [json.loads(line) for line in self.dataset.read_text().splitlines()]
        self.project = Mock()
        self.project.agents.get_version.return_value = SimpleNamespace(name="agent", version="8")
        evaluator = self.project.evaluators.get_version.return_value
        evaluator.name, evaluator.version = business.EVALUATOR_NAME, "11"
        evaluator.definition.as_dict.return_value = business.evaluator_definition()
        self.project.datasets.get.return_value = SimpleNamespace(id="canonical-reference-id")
        self.client = Mock()
        self.client.evals.list.return_value = []
        self.client.post.side_effect = [
            {"id": "eval-id"}, RunCreateResponse.model_construct(id="run-id"),
        ]
        patcher = patch.object(business, "ensure_anchors_evaluator", return_value="3")
        self.ensure_anchors = patcher.start()
        self.addCleanup(patcher.stop)

    def create(self, **kwargs):
        return business.create_agent_run(
            self.project, self.client, "endpoint", self.dataset, "agent", "8", "judge",
            **({"evaluator_version": "11"} | kwargs),
        )

    def test_existing_dataset_invokes_agent_and_maps_generated_answer_to_full_judge(self) -> None:
        with patch.object(business, "capture_case") as capture:
            record = self.create(comparison_id="pair", run_label="Fabric IQ")
        capture.assert_not_called()
        self.project.get_openai_client.assert_not_called()
        self.client.responses.create.assert_not_called()
        self.project.datasets.upload_file.assert_not_called()
        digest = hashlib.sha256(self.dataset.read_bytes()).hexdigest()
        self.project.datasets.get.assert_called_once_with(
            name=datasets.REFERENCE_NAME, version=digest[:16],
        )
        evaluation = self.client.post.call_args_list[0].kwargs["body"]
        schema = evaluation["data_source_config"]
        self.assertTrue(schema["include_sample_schema"])
        self.assertNotIn("response", schema["item_schema"]["required"])
        criterion = evaluation["testing_criteria"][0]
        self.assertEqual(criterion["name"], "Business correctness")
        self.assertEqual(criterion["evaluator_version"], "11")
        anchors = evaluation["testing_criteria"][1]
        self.assertEqual(
            (anchors["name"], anchors["evaluator_name"], anchors["evaluator_version"]),
            ("Business anchors", business.ANCHORS_NAME, "3"),
        )
        self.assertNotIn("data_mapping", anchors)
        self.assertEqual(record["evaluator"]["anchors"], {"name": business.ANCHORS_NAME, "version": "3"})
        self.assertEqual(criterion["data_mapping"], {
            field: "{{sample.output_text}}" if field == "response" else "{{item." + field + "}}"
            for field in business.FIELDS
        })
        body = self.client.post.call_args.kwargs["body"]
        source = body["data_source"]
        self.assertEqual(source["type"], "azure_ai_target_completions")
        self.assertEqual(source["source"], {"type": "file_id", "id": "canonical-reference-id"})
        self.assertEqual(source["target"], {"type": "azure_ai_agent", "name": "agent", "version": "8"})
        template = source["input_messages"]["template"]
        self.assertEqual(template[0]["content"]["text"], "{{item.query}}")
        for secret_field in ("context", "ground_truth", "criteria", "critical_errors"):
            self.assertNotIn(secret_field, json.dumps(template))
        self.assertNotIn("file_content", json.dumps(body))
        self.assertEqual(body["metadata"]["comparison_id"], "pair")
        self.assertEqual(record["dataset"]["id"], "canonical-reference-id")
        self.assertEqual(record["dataset"]["reference_sha256"], digest)
        self.assertNotIn("inline_input", record["dataset"])
        self.assertEqual(record["agent"], {"name": "agent", "version": "8"})
        self.project.evaluators.create_version.assert_not_called()
        record["output_items"] = [
            {"status": "completed", "datasource_item": case, "results": [
                {"status": "completed", "passed": case["id"] != "B-005", "reason": "Cloud judgment"},
            ]}
            for case in self.cases
        ]
        summary = comparison_summary(record)["agents"]["agent:v8"]
        self.assertEqual((summary["passed"], summary["failed"]), (5, 1))
        record["output_items"].pop()
        with self.assertRaisesRegex(ValueError, "missing"):
            comparison_summary(record)

    def test_summary_reads_results_by_name_and_keeps_the_anchor_score_separate(self) -> None:
        record = {
            "dataset": {"cases": [{"id": "B-001", "case_id": "B-001", "agent": {"name": "agent", "version": "8"}}]},
            "output_items": [{
                "status": "completed", "datasource_item": {"id": "B-001"},
                "results": [
                    {"name": "Business anchors", "status": "completed", "score": 0.5, "passed": False},
                    {"name": "Business correctness", "status": "completed", "passed": True, "reason": "ok"},
                ],
            }],
        }
        case = comparison_summary(record)["agents"]["agent:v8"]["cases"]["B-001"]
        self.assertEqual((case["passed"], case["anchor_score"]), (True, 0.5))
        record["output_items"][0]["results"][0]["status"] = "error"
        with self.assertRaisesRegex(ValueError, "anchor score"):
            comparison_summary(record)
        record["output_items"][0]["results"].pop(1)
        with self.assertRaisesRegex(ValueError, "incomplete"):
            comparison_summary(record)

    def test_anchor_evaluator_is_reused_by_code_hash_and_registered_when_absent(self) -> None:
        digest = hashlib.sha256(business.anchors_definition()["code_text"].encode()).hexdigest()
        project = Mock()
        project.evaluators.list_versions.return_value = [
            SimpleNamespace(version="1", metadata=None),
            SimpleNamespace(version="2", metadata={"code_sha256": digest}),
        ]
        self.assertEqual(ENSURE_ANCHORS(project), "2")
        project.evaluators.create_version.assert_not_called()

        project.evaluators.list_versions.side_effect = ResourceNotFoundError("missing")
        project.evaluators.create_version.return_value = SimpleNamespace(version="1")
        project.evaluators.get_version.return_value.definition.as_dict.return_value = {
            "code_text": business.anchors_definition()["code_text"],
        }
        self.assertEqual(ENSURE_ANCHORS(project), "1")
        registered = project.evaluators.create_version.call_args.kwargs["evaluator_version"]
        self.assertEqual(registered["metadata"], {"code_sha256": digest})
        self.assertEqual(registered["definition"]["type"], "code")
        self.assertEqual(registered["definition"]["init_parameters"]["properties"]["pass_threshold"]["type"], "number")

        project.evaluators.get_version.return_value.definition.as_dict.return_value = {"code_text": "other"}
        with self.assertRaisesRegex(RuntimeError, "differs from the workspace"):
            ENSURE_ANCHORS(project)

    def test_reuses_evaluation_but_creates_a_new_run_for_the_same_configuration(self) -> None:
        first = self.create(comparison_id="first", run_label="Fabric IQ")
        definition = self.client.post.call_args_list[0].kwargs["body"]
        self.client.evals.list.return_value = [
            SimpleNamespace(id="eval-id", metadata=definition["metadata"]),
        ]
        self.client.evals.retrieve.return_value.model_dump.return_value = {"id": "eval-id", **definition}
        self.client.post.side_effect = [RunCreateResponse.model_construct(id="second-run")]
        second = self.create(comparison_id="second", run_label="Fabric IQ")
        self.assertEqual(self.client.post.call_count, 3)
        self.assertEqual(first["evaluation_id"], second["evaluation_id"])
        self.assertNotEqual(first["run_id"], second["run_id"])
        self.assertEqual(first["dataset"]["id"], second["dataset"]["id"])

    def test_registration_reads_workspace_definition_and_verifies_its_hash(self) -> None:
        definition = business.evaluator_definition()
        definition["prompt_text"] += "\nAdditional workspace-only instruction."
        self.project.evaluators.create_version.return_value = SimpleNamespace(
            name=business.EVALUATOR_NAME, version="new",
        )
        self.project.evaluators.get_version.return_value.definition.as_dict.return_value = definition
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evaluator.json"
            path.write_text(json.dumps(definition))
            with patch.object(business, "EVALUATOR_PATH", path):
                record = self.create(evaluator_version=None)
        registered = self.project.evaluators.create_version.call_args.kwargs
        self.assertEqual(registered["headers"], {"Foundry-Features": "Evaluations=V1Preview"})
        self.assertEqual(registered["evaluator_version"]["definition"], definition)
        digest = hashlib.sha256(json.dumps(definition, sort_keys=True).encode()).hexdigest()
        self.assertEqual(record["evaluator"]["definition_sha256"], digest)

    def test_invalid_or_mismatched_definition_stops_before_dataset_or_run_creation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evaluator.json"
            for content, error in ((None, FileNotFoundError), ("{invalid", json.JSONDecodeError)):
                with self.subTest(content=content):
                    if content is not None:
                        path.write_text(content)
                    with patch.object(business, "EVALUATOR_PATH", path), self.assertRaises(error):
                        self.create()
        self.project.evaluators.get_version.return_value.definition.as_dict.return_value = {}
        with self.assertRaisesRegex(RuntimeError, "differs from requested definition"):
            self.create()
        self.project.datasets.get.assert_not_called()
        self.client.post.assert_not_called()

    def test_saved_answers_noncanonical_json_and_rubric_cannot_enter_direct_runs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            for suffix, row in (
                (".json", {"test_cases": self.cases}),
                (".jsonl", self.cases[0] | {"response": "Saved answer"}),
                (".jsonl", self.cases[0] | {"criteria": json.loads(self.cases[0]["criteria"])}),
            ):
                with self.subTest(suffix=suffix, keys=row.keys()):
                    path = Path(directory) / f"reference{suffix}"
                    path.write_text(json.dumps(row) + "\n")
                    with self.assertRaises(ValueError):
                        business.create_agent_run(
                            self.project, self.client, "endpoint", path, "agent", "8", "judge", "11",
                        )
        with self.assertRaisesRegex(ValueError, "custom business evaluator only"):
            self.create(evaluator_kind="rubric")
        self.project.agents.get_version.assert_not_called()
        self.project.datasets.get.assert_not_called()
        self.client.post.assert_not_called()

    def test_recorded_answer_and_calibration_runtime_is_blocked_without_cloud_calls(self) -> None:
        with self.assertRaisesRegex(ValueError, "Cloud judging.*is disabled"):
            business.create_regression_run(
                self.project, self.client, "endpoint", [], {}, "judge", "11",
            )
        self.assertEqual(self.project.mock_calls, [])
        self.assertEqual(self.client.mock_calls, [])


class BusinessEvaluationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = ROOT / "tests/fixtures/business/replay_561e13fcca05413ebdec5422a4cba9ab.json"
        self.controls = ROOT / "evaluations/business_judge_controls.json"
        self.dataset = ROOT / "evaluations/archive/business_tests_v2.json"
        self.rows, self.provenance = regression_cases(
            self.dataset, self.source, self.controls,
        )

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
        other = ROOT / "tests/fixtures/business/replay_37a9833521b0411a866a1fb88d56f950.json"
        with self.assertRaisesRegex(ValueError, "differs from current reference"):
            regression_cases(active, self.source, self.controls)
        with self.assertRaisesRegex(ValueError, "exactly one response for every reference case"):
            comparison_cases(active, [self.source, other])

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

    def test_offline_capture_validation_requires_all_cases_and_preserves_saved_answers(self) -> None:
        dataset = ROOT / "evaluations/business_tests.json"
        with tempfile.TemporaryDirectory() as directory:
            paths = self.batch_captures(directory)
            rows, provenance = captured_cases(dataset, paths, "endpoint")
            cases = json.loads(dataset.read_text())["test_cases"]
            self.assertEqual(len(rows), len(cases))
            self.assertEqual({row["case_id"] for row in rows}, {case["id"] for case in cases})
            self.assertTrue(all(row["response"] == "Saved answer 1" for row in rows))
            self.assertEqual(provenance["mode"], "comparison")
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

    def test_cli_blocks_saved_answers_calibration_and_implicit_replay_before_cloud_access(self) -> None:
        for arguments in (
            ["--captured-responses", "capture.json", "--evaluator-version", "11"],
            ["--compare-runs", "iq.json", "basic.json"],
            ["--source-run", "historical.json"],
            ["--controls", "controls.json"],
            ["--agent", "agent", "--agent-version", "8", "--evaluator-kind", "rubric"],
            [],
        ):
            with self.subTest(arguments=arguments), patch.object(sys, "argv", [
                "evaluate_business.py", "--endpoint", "endpoint", *arguments,
            ]), patch.object(cli, "AIProjectClient") as factory:
                with self.assertRaises(SystemExit) as raised:
                    cli.main()
                self.assertEqual(raised.exception.code, 2)
                factory.assert_not_called()

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
            self.assertEqual([row["response"] for row in rows], ["Saved answer 1"])
            self.assertEqual(provenance["case_id"], "B-001")


class FoundryDatasetTests(unittest.TestCase):
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
                project, client, "endpoint", ROOT / "evaluations/generated/business_reference.jsonl",
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
        for kind in ("custom",):
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
                    project, client, "endpoint", ROOT / "evaluations/generated/business_reference.jsonl",
                    "agent", "7", "gpt-4.1", None,
                    evaluator_kind=kind,
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
                    patch.object(business, "create_regression_run") as replay, \
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
