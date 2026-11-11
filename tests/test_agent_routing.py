import json
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from azure.ai.projects.models import FabricIQPreviewTool, PromptAgentDefinition

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import update_agent_instructions


class ResponseTests(unittest.TestCase):
    def check(self, response_json: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["jq", "-se", "-f", str(ROOT / "scripts/check_agent_response.jq")],
            input=response_json, text=True, capture_output=True,
        )

    def test_jq_displays_the_unmodified_response(self) -> None:
        response = {"status": "completed", "error": None, "calls": [], "answer": ["Antwort"]}
        displayed = subprocess.run(["jq", "."], input=json.dumps(response),
                                   text=True, capture_output=True, check=True)
        self.assertEqual(json.loads(displayed.stdout), response)
        self.assertEqual(self.check(displayed.stdout).returncode, 0)

    def test_failures_return_nonzero_and_remain_visible(self) -> None:
        for response in [
            {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}},
            {"status": "completed", "error": {"message": "failed"}},
            {"status": "completed", "calls": [{"name": "ask_ontology", "status": "failed", "error": "failed"}]},
            {"status": "completed", "calls": [{"name": "ask_ontology", "status": "completed",
                                               "result": '{"error":{"message":"failed"}}'}]},
            {"status": "completed", "calls": [{"name": "ask_ontology", "status": "completed",
                "result": json.dumps({"additionalProperties": {"_meta": {
                    "error_classification": "ServerError", "error_reason": "AgentFailed",
                    "error_detail": "Please retry", "error_retryable": True}}})}]},
        ]:
            with self.subTest(response=response):
                result = self.check(json.dumps(response))
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("error", result.stderr)

    def test_moderation_annotations_remain_in_the_original_response(self) -> None:
        result = {"response": "answer", "raiPostToolAnnotations": [
            {"error": {"errorDetails": "Error during output moderation"}}]}
        response = {
            "status": "completed", "calls": [
                {"name": "ask_ontology", "status": "completed", "result": json.dumps(result)}]}
        displayed = subprocess.run(["jq", "."], input=json.dumps(response),
                                   text=True, capture_output=True, check=True)
        self.assertIn("Error during output moderation", displayed.stdout)
        self.assertEqual(json.loads(displayed.stdout), response)
        self.assertEqual(self.check(displayed.stdout).returncode, 0)

    def test_invalid_json_does_not_become_a_success(self) -> None:
        for value in ["", "not JSON", "null", "{}\n{}"]:
            with self.subTest(value=value):
                result = self.check(value)
                self.assertNotEqual(result.returncode, 0)

    def test_notebook_does_not_mask_azure_request_failures(self) -> None:
        notebook = json.loads((ROOT / "notebooks/project_synonym_test.ipynb").read_text())
        sources = ["".join(c["source"]) for c in notebook["cells"] if c["cell_type"] == "code"]
        requests = [s for s in sources if "/openai/v1/responses" in s]
        self.assertEqual(len(requests), 4)
        for source in requests:
            with self.subTest(source=source.splitlines()[0]):
                self.assertIn("| jq .", source)
                self.assertNotIn("show_agent_response", source)
                stub = 'az() { printf "Simulated Azure failure\\n" >&2; return 42; };\n'
                result = subprocess.run(["bash", "-c", stub + source],
                                        capture_output=True, text=True, cwd=ROOT)
                self.assertEqual(result.returncode, 42)
                self.assertIn("Simulated Azure failure", result.stderr)
                self.assertEqual(result.stdout, "")


class InstructionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.latest = SimpleNamespace(
            version="5", metadata={"purpose": "test"}, description="existing", blueprint_reference=None,
            definition=PromptAgentDefinition(
                model="existing-model", instructions="old instructions", temperature=0.2,
                tools=[FabricIQPreviewTool(project_connection_id="existing-connection")]),
        )

    def test_only_instructions_change_when_publishing(self) -> None:
        before = self.latest.definition.as_dict()
        with patch.object(update_agent_instructions, "AIProjectClient") as factory:
            client = factory.return_value.__enter__.return_value
            client.agents.list_versions.return_value = [self.latest]
            client.agents.create_version.return_value = SimpleNamespace(version="6")
            version = update_agent_instructions.update_instructions("https://example.test", "agent")
            kwargs = client.agents.create_version.call_args.kwargs
        self.assertEqual(version, "6")
        expected = before | {"instructions": update_agent_instructions.INSTRUCTIONS.read_text()}
        self.assertEqual(kwargs["definition"].as_dict(), expected)
        self.assertEqual(self.latest.definition.as_dict(), before)
        self.assertEqual(kwargs["metadata"], self.latest.metadata)

    def test_unchanged_instructions_do_not_create_versions(self) -> None:
        self.latest.definition.instructions = update_agent_instructions.INSTRUCTIONS.read_text()
        with patch.object(update_agent_instructions, "AIProjectClient") as factory:
            client = factory.return_value.__enter__.return_value
            client.agents.list_versions.return_value = [self.latest]
            self.assertEqual(update_agent_instructions.update_instructions("https://example.test", "agent"), "5")
            client.agents.create_version.assert_not_called()

    def test_missing_agent_version_is_an_error(self) -> None:
        with patch.object(update_agent_instructions, "AIProjectClient") as factory:
            factory.return_value.__enter__.return_value.agents.list_versions.return_value = []
            with self.assertRaisesRegex(ValueError, "No published version"):
                update_agent_instructions.update_instructions("https://example.test", "agent")


if __name__ == "__main__":
    unittest.main()
