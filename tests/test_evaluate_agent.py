import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from azure.core.exceptions import ResourceNotFoundError
from openai.types import EvalCreateResponse
from openai.types.evals import RunCreateResponse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import evaluate_agent


class EvaluationTests(unittest.TestCase):
    def test_six_case_dataset_is_valid_agent_target_input(self) -> None:
        cases = evaluate_agent.load_cases(ROOT / "evaluations/generated/business_reference.jsonl")
        self.assertEqual(len(cases), 6)

    def test_invalid_missing_duplicate_and_fabricated_rows_fail(self) -> None:
        case = {"id": "test", "query": "question", "context": "evidence", "ground_truth": "answer"}
        invalid = [
            "", "[]", json.dumps(case | {"query": ""}),
            json.dumps(case | {"response": "fabricated"}),
            json.dumps(case) + "\n" + json.dumps(case),
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data.jsonl"
            for content in invalid:
                with self.subTest(content=content):
                    path.write_text(content)
                    with self.assertRaises(ValueError):
                        evaluate_agent.load_cases(path)

    def test_only_query_is_sent_to_the_pinned_agent(self) -> None:
        body = evaluate_agent.run_body("dataset-id", "agent", "7")
        source = body["data_source"]
        self.assertEqual(source["type"], "azure_ai_target_completions")
        self.assertEqual(source["source"], {"type": "file_id", "id": "dataset-id"})
        self.assertEqual(source["target"], {"type": "azure_ai_agent", "name": "agent", "version": "7"})
        messages = source["input_messages"]["template"]
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["role"], "user")
        self.assertEqual(messages[0]["content"]["text"], "{{item.query}}")
        self.assertNotIn("context", json.dumps(messages))
        self.assertNotIn("ground_truth", json.dumps(messages))

    def test_groundedness_maps_generated_answer_and_reference_context(self) -> None:
        body = evaluate_agent.evaluation_body("judge", "19")
        criterion = body["testing_criteria"][0]
        self.assertEqual(criterion["evaluator_name"], "builtin.groundedness")
        self.assertEqual(criterion["evaluator_version"], "19")
        self.assertEqual(criterion["initialization_parameters"], {
            "deployment_name": "judge", "threshold": 3, "evaluation_level": "turn",
        })
        self.assertEqual(criterion["data_mapping"], {
            "query": "{{item.query}}", "response": "{{sample.output_text}}", "context": "{{item.context}}",
        })
        self.assertTrue(body["data_source_config"]["include_sample_schema"])

    def test_upload_and_evaluation_use_current_agent_without_modifying_it(self) -> None:
        project = Mock()
        project.agents.list_versions.return_value = [SimpleNamespace(name="agent", version="7")]
        project.evaluators.get_version.return_value.version = "19"
        project.datasets.get.side_effect = ResourceNotFoundError("missing")
        project.datasets.upload_file.return_value = SimpleNamespace(
            id="dataset-id", name=evaluate_agent.DATASET_NAME, version="content-hash",
        )
        evaluation = EvalCreateResponse.model_construct(id="eval-id")
        run = RunCreateResponse.model_construct(id="run-id")
        client = Mock()
        client.post.side_effect = [evaluation, run]
        record = evaluate_agent.create_run(
            project, client, "https://foundry.example", ROOT / "evaluations/generated/business_reference.jsonl",
            "agent", None, "judge",
        )
        self.assertEqual(record["agent"], {"name": "agent", "version": "7"})
        self.assertEqual(record["dataset"]["case_count"], 6)
        project.datasets.upload_file.assert_called_once()
        project.agents.create_version.assert_not_called()
        self.assertEqual(client.post.call_args_list[1].args[0], "/evals/eval-id/runs")
        self.assertEqual(client.post.call_args_list[1].kwargs["body"]["data_source"]["target"]["version"], "7")

    def test_existing_dataset_is_reused_and_explicit_version_is_respected(self) -> None:
        project = Mock()
        project.agents.get_version.return_value = SimpleNamespace(name="agent", version="6")
        project.evaluators.get_version.return_value.version = "19"
        project.datasets.get.return_value = SimpleNamespace(
            id="dataset-id", name=evaluate_agent.DATASET_NAME, version="content-hash",
        )
        client = Mock()
        client.post.side_effect = [
            EvalCreateResponse.model_construct(id="eval-id"),
            RunCreateResponse.model_construct(id="run-id"),
        ]
        record = evaluate_agent.create_run(
            project, client, "https://foundry.example", ROOT / "evaluations/generated/business_reference.jsonl",
            "agent", "6", "judge",
        )
        self.assertEqual(record["agent"]["version"], "6")
        project.datasets.upload_file.assert_not_called()
        project.agents.get_version.assert_called_once_with(agent_name="agent", agent_version="6")

    def test_timeout_preserves_run_state_for_resume(self) -> None:
        client = Mock()
        run = Mock(status="in_progress")
        run.model_dump.return_value = {"id": "run-id", "status": "in_progress"}
        client.evals.runs.retrieve.return_value = run
        record = {"evaluation_id": "eval-id", "run_id": "run-id"}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result.json"
            with patch.object(evaluate_agent.time, "monotonic", side_effect=[0, 2]):
                with self.assertRaisesRegex(TimeoutError, "--resume"):
                    evaluate_agent.wait_for_run(client, record, path, timeout=1)
            self.assertEqual(json.loads(path.read_text())["run"]["status"], "in_progress")

    def test_errors_and_missing_items_remain_explicit_in_saved_results(self) -> None:
        client = Mock()
        client.evals.runs.output_items.list.return_value = []
        run = Mock(status="completed", report_url="https://foundry.example/report")
        run.result_counts.errored = 1
        run.result_counts.model_dump.return_value = {"errored": 1}
        record = {"evaluation_id": "eval-id", "run_id": "run-id", "dataset": {"case_count": 10}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result.json"
            with self.assertRaisesRegex(RuntimeError, "errored or missing"):
                evaluate_agent.finish_run(client, record, run, path)
            self.assertEqual(json.loads(path.read_text())["summary"]["result_counts"]["errored"], 1)


if __name__ == "__main__":
    unittest.main()
