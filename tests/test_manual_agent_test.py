import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from openai import OpenAIError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import manual_agent_test as manual


class ManualAgentTestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cases, self.digest = manual.load_dataset(manual.DATASET)
        self.case = self.cases[0]
        self.project = MagicMock()
        self.client = self.project.get_openai_client.return_value.__enter__.return_value
        self.create = self.client.responses.with_raw_response.create
        raw_response = self.create.return_value
        response = raw_response.parse.return_value
        response.status = "completed"
        response.output_text = "Captured answer, not a reference."
        raw_response.http_response.json.return_value = {"id": "response-id", "output": []}

    def reviewed(self) -> dict:
        review = manual.review_template(self.case)
        for item in review["facts"].values():
            item.update(status="correct", evidence="Answer excerpt checked by reviewer")
        for error in review["critical_errors"].values():
            error["present"] = False
        review["source_support"].update(status="correct", evidence="Cited clauses checked")
        review["additional_incorrect_claims"] = []
        return review

    def test_direct_capture_sends_only_query_and_reuses_without_overwriting_review(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "B-001.json"
            record = manual.capture_case(
                self.project, "https://foundry.example", "agent", "7",
                self.case, self.digest, path,
            )
            self.create.assert_called_once_with(
                input=self.case["query"],
                extra_body={"agent_reference": {
                    "type": "agent_reference", "name": "agent", "version": "7",
                }},
            )
            self.assertEqual(record["status"], "captured")
            self.assertEqual(manual.assess_review(self.case, record["review"])["outcome"], "pending")
            record["review"] = self.reviewed()
            path.write_text(json.dumps(record))
            reused = manual.capture_case(
                self.project, "https://foundry.example", "agent", "7",
                self.case, self.digest, path,
            )
            self.assertEqual(reused["review"], record["review"])
            self.assertEqual(self.create.call_count, 1)
            with self.assertRaisesRegex(ValueError, "different dataset"):
                manual.capture_case(
                    self.project, "https://foundry.example", "agent", "7",
                    self.case, "different-hash", path,
                )

    def test_failed_or_empty_capture_is_saved_and_not_reused_as_success(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            for mode in ("api_error", "empty"):
                with self.subTest(mode=mode):
                    path = Path(directory) / f"{mode}.json"
                    if mode == "api_error":
                        self.create.side_effect = OpenAIError("request failed")
                        expected = OpenAIError
                    else:
                        self.create.side_effect = None
                        self.create.return_value.parse.return_value.output_text = ""
                        expected = RuntimeError
                    with self.assertRaises(expected):
                        manual.capture_case(
                            self.project, "endpoint", "agent", "7", self.case, self.digest, path,
                        )
                    self.assertEqual(json.loads(path.read_text())["status"], "error")
                    with self.assertRaisesRegex(ValueError, "incomplete or failed"):
                        manual.capture_case(
                            self.project, "endpoint", "agent", "7", self.case, self.digest, path,
                        )

    def test_capture_preserves_azure_preview_tool_json_without_reserializing(self) -> None:
        raw_response = self.create.return_value
        payload = {
            "id": "response-id",
            "tools": [{
                "type": "fabric_iq_preview",
                "fabric_iq": {"connection_id": "connection-id"},
            }],
            "output": [{
                "type": "mcp_call", "name": "ask_ontology",
                "arguments": json.dumps({"request": self.case["query"]}),
            }],
            "provider_metadata": {"preview": True},
        }
        raw_response.http_response.json.return_value = payload
        raw_response.parse.return_value.model_dump.side_effect = UserWarning(
            "Pydantic serializer warnings: unexpected fabric_iq_preview tool"
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "B-001.json"
            manual.capture_case(
                self.project, "endpoint", "agent", "7", self.case, self.digest, path,
            )
            saved = json.loads(path.read_text())
            self.assertEqual(saved["response"], payload)
            self.assertEqual(saved["answer"], "Captured answer, not a reference.")
            self.assertEqual(saved["status"], "captured")

    def test_first_fabric_request_must_preserve_the_entire_query(self) -> None:
        def payload(request):
            return {
                "tools": [{"type": "fabric_iq_preview"}],
                "output": [{"type": "mcp_call", "name": "ask_ontology",
                            "arguments": json.dumps({"request": request})}],
            }

        query = self.case["query"]
        manual.validate_fabric_handoff(payload(query), query)
        manual.validate_fabric_handoff(payload(" ".join(query.split())), query)
        followup = payload(query)
        followup["output"].extend(payload("Retrieve the project manager")["output"])
        manual.validate_fabric_handoff(followup, query)
        truncated = payload("Bewerte P-100: Genehmigung und Terminpuffer.")
        variants = [truncated, payload(None), payload(query) | {"output": []}]
        later_complete = copy.deepcopy(truncated)
        later_complete["output"].extend(payload(query)["output"])
        variants.append(later_complete)
        for response in variants:
            with self.subTest(response=response):
                with self.assertRaisesRegex(ValueError, "Fabric IQ handoff"):
                    manual.validate_fabric_handoff(response, query)
        manual.validate_fabric_handoff({"tools": [], "output": []}, query)

    def test_truncated_handoff_is_saved_as_error_without_retry_or_reuse(self) -> None:
        payload = {
            "id": "response-id", "status": "completed",
            "tools": [{"type": "fabric_iq_preview"}],
            "output": [{"type": "mcp_call", "name": "ask_ontology",
                        "arguments": json.dumps({"request": "Only the subject"})}],
        }
        self.create.return_value.http_response.json.return_value = payload
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "B-001.json"
            with self.assertRaisesRegex(ValueError, "complete original user message"):
                manual.capture_case(
                    self.project, "endpoint", "agent", "8", self.case, self.digest, path,
                )
            failed = json.loads(path.read_text())
            self.assertEqual(failed["status"], "error")
            self.assertEqual(failed["response"], payload)
            self.assertEqual(self.create.call_count, 1)
            failed["status"] = "captured"
            failed.pop("error")
            path.write_text(json.dumps(failed))
            with self.assertRaisesRegex(ValueError, "complete original user message"):
                manual.capture_case(
                    self.project, "endpoint", "agent", "8", self.case, self.digest, path,
                )
            self.assertEqual(json.loads(path.read_text())["status"], "error")
            self.assertEqual(self.create.call_count, 1)

    def test_critical_error_and_missing_fact_cannot_be_averaged_away(self) -> None:
        review = self.reviewed()
        self.assertEqual(manual.assess_review(self.case, review)["outcome"], "passed")
        review["critical_errors"]["wrong_deadline"].update(present=True, evidence="Eight weeks")
        assessment = manual.assess_review(self.case, review)
        self.assertEqual(assessment["fulfilled_fraction"], 1)
        self.assertEqual(assessment["outcome"], "failed")
        review = self.reviewed()
        review["facts"]["escalation"].update(status="missing", evidence="No deadline in answer")
        assessment = manual.assess_review(self.case, review)
        self.assertEqual(assessment["answered_fact_accuracy"], 1)
        self.assertLess(assessment["fulfilled_fraction"], 1)
        self.assertEqual(assessment["outcome"], "failed")
        self.assertEqual(assessment["missing_facts"], 1)

    def test_sources_extra_claims_and_unreviewed_checks_gate_success(self) -> None:
        for field in ("sources", "extra_claims", "unreviewed"):
            with self.subTest(field=field):
                review = self.reviewed()
                if field == "sources":
                    review["source_support"].update(status="incorrect", evidence="Wrong clause")
                elif field == "extra_claims":
                    review["additional_incorrect_claims"] = ["Invented risk allocation"]
                else:
                    review["critical_errors"]["wrong_deadline"]["present"] = None
                self.assertNotEqual(manual.assess_review(self.case, review)["outcome"], "passed")
        review = self.reviewed()
        review["facts"]["project"]["evidence"] = ""
        with self.assertRaisesRegex(ValueError, "evidence"):
            manual.assess_review(self.case, review)
        review = self.reviewed()
        review["critical_errors"]["wrong_deadline"]["present"] = "false"
        with self.assertRaisesRegex(ValueError, "true, false or null"):
            manual.assess_review(self.case, review)

    def test_summary_reports_missing_agent_responses_and_rejects_stale_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = manual.summarize(root, self.cases, self.digest, ["agent:7", "baseline:2"])
            self.assertEqual(report["counts"]["missing_response"], 2 * len(self.cases))
            self.assertFalse(report["complete"])
            path = manual.response_path(root, "agent", "7", self.case["id"])
            record = manual.capture_case(
                self.project, "endpoint", "agent", "7", self.case, self.digest, path,
            )
            record["review"] = self.reviewed()
            path.write_text(json.dumps(record))
            report = manual.summarize(root, self.cases, self.digest, ["agent:7", "baseline:2"])
            self.assertEqual(report["counts"]["passed"], 1)
            self.assertEqual(report["counts"]["missing_response"], 2 * len(self.cases) - 1)
            changed = copy.deepcopy(self.cases)
            changed[0]["query"] = "changed prompt"
            with self.assertRaisesRegex(ValueError, "does not match"):
                manual.summarize(root, changed, self.digest, ["agent:7"])


if __name__ == "__main__":
    unittest.main()
