import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from azure.ai.projects.models import FabricIQPreviewTool, PromptAgentDefinition

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import setup_baseline_agent as baseline


class BaselineSetupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.credential = Mock()
        self.credential.get_token.return_value.token = "test-token"
        self.session = Mock()
        self.snapshot = {"tables": {table: [] for table in baseline.TABLES + baseline.SUPPLY_TABLES + baseline.PORTFOLIO_TABLES}}
        self.snapshot["tables"]["projects"] = [{"projects[ProjectId]": "P-100"}]

    def test_definition_preserves_model_settings_but_no_tools_or_skills(self) -> None:
        reference = SimpleNamespace(definition=PromptAgentDefinition(
            model="existing-model", temperature=0.2, top_p=0.8,
            instructions="Use Fabric IQ.",
            tools=[FabricIQPreviewTool(project_connection_id="connection", require_approval="never")],
        ))
        snapshot = self.snapshot
        snapshot["ontology_definition"] = {"rule.tmdl": "rule EmailCriticality"}
        snapshot["semantic_model_definition"] = {"tasks.tmdl": "measure Duration Days"}
        definition = baseline.baseline_definition(reference, snapshot)
        self.assertEqual(definition.model, "existing-model")
        self.assertEqual(definition.temperature, 0.2)
        self.assertEqual(definition.top_p, 0.8)
        self.assertEqual(definition.tools, [])
        self.assertNotIn("skills", definition.as_dict())
        self.assertNotIn("Use Fabric IQ.", definition.instructions)
        self.assertIn('"projects[ProjectId]": "P-100"', definition.instructions)
        self.assertNotIn("EmailCriticality", definition.instructions)
        self.assertNotIn("Duration Days", definition.instructions)
        self.assertNotIn(".tmdl", definition.instructions)

    def test_capture_includes_all_records_without_reading_repository_definitions(self) -> None:
        with patch.object(
            baseline, "table_rows", side_effect=lambda _s, _c, t: [{"source": t}],
        ) as rows, patch.object(Path, "read_text", side_effect=AssertionError("Repository read")):
            snapshot = baseline.capture_snapshot(self.session, self.credential)
        self.assertEqual(rows.call_count, 10)
        self.assertEqual(set(snapshot["tables"]), set(baseline.TABLES + baseline.SUPPLY_TABLES + baseline.PORTFOLIO_TABLES))
        self.assertEqual(snapshot["tables"]["documents"], [{"source": "documents"}])
        self.assertNotIn("ontology_definition", snapshot)
        self.assertNotIn("semantic_model_definition", snapshot)

    def test_data_snapshot_rejects_missing_tables_or_invalid_rows(self) -> None:
        with self.assertRaisesRegex(ValueError, "six project data tables"):
            baseline.data_only_snapshot({"tables": {}})
        self.snapshot["tables"]["documents"] = "not records"
        with self.assertRaisesRegex(ValueError, "documents"):
            baseline.data_only_snapshot(self.snapshot)

    def test_supply_records_are_included_but_partial_supply_snapshots_are_rejected(self) -> None:
        self.snapshot["tables"]["material_deliveries"] = [{"DeliveryId": "DL-100", "TaskId": "T-104"}]
        self.assertEqual(
            baseline.data_only_snapshot(self.snapshot)["tables"]["material_deliveries"],
            [{"DeliveryId": "DL-100", "TaskId": "T-104"}],
        )
        del self.snapshot["tables"]["materials"]
        with self.assertRaisesRegex(ValueError, "all three supply tables"):
            baseline.data_only_snapshot(self.snapshot)
        legacy = {"tables": {table: [] for table in baseline.TABLES}}
        self.assertEqual(baseline.data_only_snapshot(legacy), legacy)
        legacy_supply = {"tables": {table: [] for table in baseline.TABLES + baseline.SUPPLY_TABLES}}
        self.assertEqual(baseline.data_only_snapshot(legacy_supply), legacy_supply)

    def test_portfolio_snapshot_preserves_potential_sources_without_project_deliveries(self) -> None:
        self.snapshot["tables"]["suppliers"] = [{"SupplierId": "S-200", "Name": "IsolierPartner GmbH"}]
        self.snapshot["tables"]["supplier_materials"] = [{"SupplierId": "S-200", "MaterialId": "MAT-035"}]
        self.assertEqual(baseline.data_only_snapshot(self.snapshot)["tables"], self.snapshot["tables"])
        self.assertEqual(self.snapshot["tables"]["material_deliveries"], [])
        incomplete = {"tables": {table: [] for table in baseline.TABLES + baseline.PORTFOLIO_TABLES}}
        with self.assertRaisesRegex(ValueError, "all three supply tables"):
            baseline.data_only_snapshot(incomplete)

    def test_table_query_requests_all_columns_and_nulls(self) -> None:
        self.session.post.return_value.json.return_value = {
            "results": [{"tables": [{"rows": [{"documents[Content]": "Full contract text"}]}]}],
        }
        rows = baseline.table_rows(self.session, self.credential, "documents")
        self.assertEqual(rows, [{"documents[Content]": "Full contract text"}])
        kwargs = self.session.post.call_args.kwargs
        self.assertEqual(kwargs["json"], {
            "queries": [{"query": "EVALUATE 'documents'"}],
            "serializerSettings": {"includeNulls": True},
        })
        self.assertEqual(kwargs["headers"], {"Authorization": "Bearer test-token"})

    def test_table_query_rejects_semantic_errors_despite_http_success(self) -> None:
        for body in [
            {"error": {"message": "failed"}},
            {"results": [{"error": {"message": "failed"}}]},
            {"results": [{"tables": [{"rows": [], "error": {"message": "truncated"}}]}]},
        ]:
            with self.subTest(body=body):
                self.session.post.return_value.json.return_value = body
                with self.assertRaises(RuntimeError):
                    baseline.table_rows(self.session, self.credential, "projects")

    def test_table_query_reports_http_error_details(self) -> None:
        self.session.post.return_value.ok = False
        self.session.post.return_value.status_code = 400
        self.session.post.return_value.text = "Failed to open the MSOLAP connection."
        with self.assertRaisesRegex(RuntimeError, "HTTP 400 for projects: Failed to open"):
            baseline.table_rows(self.session, self.credential, "projects")

    def test_reusing_old_snapshot_strips_metadata_and_does_not_query_fabric(self) -> None:
        project = Mock()
        project.agents.list_versions.return_value = [SimpleNamespace(
            name="project-planning-agent", version="7",
            definition=PromptAgentDefinition(model="existing-model"),
        )]
        project.agents.create_version.return_value = SimpleNamespace(
            name=baseline.BASELINE_AGENT_NAME, version="2",
        )
        project.agents.get_version.side_effect = lambda **_kwargs: SimpleNamespace(
            definition=project.agents.create_version.call_args.kwargs["definition"],
        )
        self.snapshot["ontology_definition"] = {"Project.tmdl": "synonym 'qwert'"}
        self.snapshot["semantic_model_definition"] = {"tasks.tmdl": "measure Duration Days"}
        self.snapshot["capture_started_at"] = "original capture time"
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "old.json"
            source.write_text(json.dumps({"snapshot": self.snapshot}))
            record = baseline.setup_baseline(
                project, self.session, self.credential, Path(directory) / "new.json", None, source,
            )
        self.session.post.assert_not_called()
        self.assertEqual(record["snapshot"]["tables"], self.snapshot["tables"])
        self.assertEqual(record["snapshot"]["capture_started_at"], "original capture time")
        self.assertNotIn("qwert", record["definition"]["instructions"])
        self.assertNotIn("ontology_definition", record["snapshot"])
        self.assertNotIn("semantic_model_definition", record["snapshot"])
        self.assertEqual(record["snapshot_source"]["type"], "saved_snapshot")

    def test_setup_only_creates_baseline_and_does_not_run_inference(self) -> None:
        project = Mock()
        reference = SimpleNamespace(
            name="project-planning-agent", version="12",
            definition=PromptAgentDefinition(model="existing-model", instructions="Use tool."),
        )
        project.agents.list_versions.return_value = [reference]
        project.agents.create_version.return_value = SimpleNamespace(
            name=baseline.BASELINE_AGENT_NAME, version="1",
        )
        project.agents.get_version.side_effect = lambda **_kwargs: SimpleNamespace(
            definition=project.agents.create_version.call_args.kwargs["definition"],
        )
        snapshot = self.snapshot
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(baseline, "capture_snapshot", return_value=snapshot):
            output = Path(directory) / "snapshot.json"
            record = baseline.setup_baseline(project, self.session, self.credential, output, None)
            persisted = json.loads(output.read_text())
        self.assertEqual(record, persisted)
        self.assertEqual(record["reference_agent"]["version"], "12")
        self.assertEqual(record["agent"]["name"], baseline.BASELINE_AGENT_NAME)
        self.assertEqual(record["definition"]["tools"], [])
        project.agents.create_version.assert_called_once()
        self.assertEqual(
            project.agents.create_version.call_args.kwargs["agent_name"],
            baseline.BASELINE_AGENT_NAME,
        )
        project.get_openai_client.assert_not_called()

    def test_setup_rejects_missing_reference_agent(self) -> None:
        project = Mock()
        project.agents.list_versions.return_value = []
        with self.assertRaisesRegex(ValueError, "No published version"):
            baseline.setup_baseline(
                project, self.session, self.credential, Path("unused.json"), None,
            )
        self.session.post.assert_not_called()
        project.agents.create_version.assert_not_called()


if __name__ == "__main__":
    unittest.main()
