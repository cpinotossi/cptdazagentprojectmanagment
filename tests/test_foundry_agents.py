import copy
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from azure.ai.projects.models import FabricIQPreviewTool, PromptAgentDefinition

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import foundry_agents as release
import setup_agent
from setup_baseline_agent import TABLES


def version(name, number, definition, metadata=None):
    return SimpleNamespace(
        name=name, version=number, definition=definition,
        metadata=metadata or {}, draft=False, status="active",
    )


class FoundryAgentReleaseTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = {
            "capture_started_at": "2026-01-01T10:00:00+00:00",
            "capture_completed_at": "2026-01-01T10:01:00+00:00",
            "workspace_id": "synthetic-workspace",
            "semantic_model_id": "synthetic-model",
            "capture_note": "Synthetic offline test records, not a live capture.",
            "tables": {table: [] for table in TABLES},
        }
        self.snapshot["tables"]["projects"] = [{"ProjectId": "test-project"}]
        self.snapshot["tables"]["documents"] = [{"DocumentId": "test-document", "Content": "Test evidence."}]
        self.config = release.load_config()
        self.baseline = version(release.BASELINE_AGENT_NAME, "20", PromptAgentDefinition(
            model="old-baseline-model", tools=[],
            instructions=release.POLICY + json.dumps(self.snapshot, ensure_ascii=False),
        ))
        self.sources = {
            (release.BASELINE_AGENT_NAME, "20"): self.baseline,
        }
        self.published = {}
        self.project = Mock()
        self.project.agents.get_version.side_effect = lambda *, agent_name, agent_version: (
            self.sources | self.published
        )[(agent_name, agent_version)]
        self.project.agents.list_versions.side_effect = lambda *, agent_name, **_: [
            agent for (name, _), agent in self.published.items() if name == agent_name
        ]

        def create(*, agent_name, definition, metadata, **_):
            agent = version(agent_name, str(100 + len(self.published)), copy.deepcopy(definition), metadata)
            self.published[(agent_name, agent.version)] = agent
            return agent

        self.project.agents.create_version.side_effect = create
        self.manifest = Mock(spec=Path)

    def publish(self):
        return release.publish(self.project, self.config, "20", self.manifest)

    def test_definitions_use_committed_builder_settings_and_exact_original_question_prompt(self):
        with patch.object(setup_agent, "AGENT_SETTINGS", {"temperature": 0.2, "top_p": 0.8}):
            definitions = release.release_definitions(self.config, self.baseline)
            expected = setup_agent.agent_definition(self.config["model"], self.config["connection_id"]).as_dict()
        iq = definitions[release.AGENT_NAME].as_dict()
        self.assertEqual(iq, expected)
        self.assertIn("ENTIRE current user message copied verbatim", iq["instructions"])
        baseline = definitions[release.BASELINE_AGENT_NAME]
        self.assertEqual((baseline.model, baseline.temperature, baseline.top_p), (self.config["model"], 0.2, 0.8))
        self.assertEqual(baseline.tools, [])
        self.assertEqual(json.loads(baseline.instructions[len(release.POLICY):]), self.snapshot)
        self.assertNotIn("ask_ontology", baseline.instructions)
        self.assertEqual(self.baseline.definition.model, "old-baseline-model")

    def test_default_check_never_constructs_credentials_or_cloud_client(self):
        with patch.object(sys, "argv", ["foundry_agents.py"]), \
                patch.object(release, "AzureCliCredential") as credential, \
                patch.object(release, "AIProjectClient") as client, \
                patch("builtins.print") as output:
            release.main()
        credential.assert_not_called()
        client.assert_not_called()
        self.assertFalse(json.loads(output.call_args.args[0])["cloud_access"])

    def test_mismatched_basic_reference_fails_before_cloud_access(self):
        with patch.object(release, "BASELINE_REFERENCE_AGENT_NAME", "different-agent"), \
                patch.object(sys, "argv", ["foundry_agents.py"]), \
                patch.object(release, "AzureCliCredential") as credential, \
                self.assertRaisesRegex(ValueError, "Basic reference_agent"):
            release.main()
        credential.assert_not_called()

    def test_publish_requires_confirmation_and_explicit_versions_before_auth(self):
        for args in [
            ["publish"],
            ["publish", "--confirm"],
            ["publish", "--confirm", "--baseline-source-version", "latest"],
        ]:
            with self.subTest(args=args), patch.object(sys, "argv", ["foundry_agents.py", *args]), \
                    patch.object(release, "AzureCliCredential") as credential, \
                    self.assertRaises((ValueError, SystemExit)):
                release.main()
            credential.assert_not_called()

    def test_release_creates_both_immutable_versions_reads_back_and_reuses_on_retry(self):
        first = self.publish()
        self.assertEqual(first["status"], "published")
        self.assertEqual([a["name"] for a in first["agents"]], [release.AGENT_NAME, release.BASELINE_AGENT_NAME])
        self.assertEqual(self.project.agents.create_version.call_count, 2)
        self.assertEqual(self.project.agents.get_version.call_count, 3)
        for call in self.project.agents.create_version.call_args_list:
            self.assertFalse(call.kwargs["draft"])
        second = self.publish()
        self.assertEqual(self.project.agents.create_version.call_count, 2)
        self.assertTrue(all(a["reused"] for a in second["agents"]))
        self.assertEqual(first["sources"], second["sources"])
        self.assertEqual([a["version"] for a in first["agents"]], [a["version"] for a in second["agents"]])
        summary = self.manifest.write_text.call_args.args[0]
        self.assertNotIn("test-document", summary)
        self.assertNotIn("instructions", summary)
        self.assertEqual(json.loads(summary), second)
        self.project.get_openai_client.assert_not_called()
        self.project.connections.get.assert_not_called()

    def test_sources_are_strictly_pinned_and_invalid_baseline_prevents_either_creation(self):
        self.baseline.definition.instructions = "Unknown baseline policy"
        with self.assertRaisesRegex(ValueError, "snapshot marker"):
            self.publish()
        self.project.agents.create_version.assert_not_called()
        self.project.agents.list_versions.assert_not_called()

    def test_committed_baseline_policy_changes_reuse_only_frozen_evidence(self):
        self.baseline.definition.instructions = (
            "Historical policy deliberately ignored.\n\n"
            + release.SNAPSHOT_MARKER + json.dumps(self.snapshot)
        )
        with patch("setup_baseline_agent.POLICY", "New committed policy.\n\n" + release.SNAPSHOT_MARKER):
            result = self.publish()
        baseline = self.published[(release.BASELINE_AGENT_NAME, result["agents"][1]["version"])].definition
        self.assertTrue(baseline.instructions.startswith("New committed policy."))
        self.assertNotIn("Historical policy", baseline.instructions)
        self.assertEqual(release.snapshot_from_agent(version("baseline", "1", baseline)), self.snapshot)

    def test_rejects_empty_partial_or_unprovenanced_snapshots(self):
        mutations = [
            lambda s: s["tables"].update(projects=[]),
            lambda s: s["tables"].pop("documents"),
            lambda s: s.pop("capture_started_at"),
            lambda s: s.update(capture_completed_at="2025-01-01T00:00:00+00:00"),
            lambda s: s.update(capture_started_at="2026-01-01T10:00:00"),
            lambda s: s.update(ontology_definition={"rules": "not data"}),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                snapshot = copy.deepcopy(self.snapshot)
                mutate(snapshot)
                self.baseline.definition.instructions = release.POLICY + json.dumps(snapshot)
                with self.assertRaises(ValueError):
                    self.publish()
        self.project.agents.create_version.assert_not_called()

    def test_baseline_with_tools_or_non_prompt_definition_is_rejected(self):
        for definition in [
            PromptAgentDefinition(model="test", tools=[FabricIQPreviewTool(
                project_connection_id=self.config["connection_id"], require_approval="never",
            )]),
            SimpleNamespace(instructions="Not a prompt definition"),
        ]:
            with self.subTest(definition=definition):
                self.baseline.definition = definition
                with self.assertRaises(ValueError):
                    release.release_definitions(self.config, self.baseline)

    def test_configuration_rejects_mismatched_projects_and_unknown_fields_offline(self):
        mutations = [
            {"endpoint": "https://different.services.ai.azure.com/api/projects/project-planning"},
            {"connection_id": "/connections/other"},
            {"model": ""},
            {"endpoint": self.config["endpoint"] + "?secret=value"},
            {"endpoint": self.config["endpoint"].replace("https://", "https://user:password@")},
            {"settings": {"temperature": 0.5}},
        ]
        for changes in mutations:
            with self.subTest(changes=changes), patch.object(
                Path, "read_text", return_value=json.dumps(self.config | changes),
            ), self.assertRaises(ValueError):
                release.load_config()

    def test_model_and_settings_changes_publish_from_sources_without_cloud_reference(self):
        first = self.publish()
        self.config["model"] = "new-intended-deployment"
        with patch.object(setup_agent, "AGENT_SETTINGS", {"temperature": 0.3}):
            second = self.publish()
        self.assertEqual(self.project.agents.create_version.call_count, 4)
        self.assertNotEqual(first["agents"][0]["definition_sha256"], second["agents"][0]["definition_sha256"])
        for entry in second["agents"]:
            definition = self.published[(entry["name"], entry["version"])].definition
            self.assertEqual((definition.model, definition.temperature), ("new-intended-deployment", 0.3))
        iq_versions_read = [
            call.kwargs["agent_version"] for call in self.project.agents.get_version.call_args_list
            if call.kwargs["agent_name"] == release.AGENT_NAME
        ]
        self.assertTrue(all(int(number) >= 100 for number in iq_versions_read))

    def test_tool_intent_changes_in_shared_builder_are_not_overridden_by_release(self):
        build = release.agent_definition

        def changed_builder(model, connection):
            definition = build(model, connection)
            definition.tools[0].require_approval = "always"
            return definition

        with patch.object(release, "agent_definition", side_effect=changed_builder):
            result = self.publish()
        iq = self.published[(release.AGENT_NAME, result["agents"][0]["version"])].definition
        self.assertEqual(iq.tools[0].require_approval, "always")
        baseline = self.published[(release.BASELINE_AGENT_NAME, result["agents"][1]["version"])].definition
        self.assertEqual(baseline.tools, [])

    def test_draft_failed_or_wrong_source_identity_is_rejected(self):
        for field, value in [("draft", True), ("status", "failed"), ("name", "unexpected"), ("version", "99")]:
            with self.subTest(field=field):
                old = getattr(self.baseline, field)
                setattr(self.baseline, field, value)
                with self.assertRaises(RuntimeError):
                    self.publish()
                setattr(self.baseline, field, old)
        self.project.agents.create_version.assert_not_called()

    def test_hash_metadata_does_not_replace_exact_readback_verification(self):
        first = self.publish()
        agent = self.published[(release.AGENT_NAME, first["agents"][0]["version"])]
        agent.definition.instructions = "Remote drift despite matching metadata"
        with self.assertRaisesRegex(RuntimeError, "Definition readback differs"):
            self.publish()
        self.assertEqual(self.project.agents.create_version.call_count, 2)

    def test_explicit_readback_mismatch_is_not_reported_as_published(self):
        original_get = self.project.agents.get_version.side_effect

        def get(**kwargs):
            agent = original_get(**kwargs)
            if int(agent.version) >= 100:
                agent = copy.deepcopy(agent)
                agent.version = "999"
            return agent

        self.project.agents.get_version.side_effect = get
        with self.assertRaisesRegex(RuntimeError, "wrong version"):
            self.publish()
        record = json.loads(self.manifest.write_text.call_args.args[0])
        self.assertEqual(record["status"], "publishing")
        self.assertEqual(record["agents"], [])

    def test_partial_pair_failure_retains_first_result_and_retry_finishes_pair(self):
        create = self.project.agents.create_version.side_effect

        def fail_second(**kwargs):
            if kwargs["agent_name"] == release.BASELINE_AGENT_NAME:
                raise RuntimeError("synthetic interrupted release")
            return create(**kwargs)

        self.project.agents.create_version.side_effect = fail_second
        with self.assertRaisesRegex(RuntimeError, "interrupted"):
            self.publish()
        partial = json.loads(self.manifest.write_text.call_args.args[0])
        self.assertEqual(partial["status"], "publishing")
        self.assertEqual(len(partial["agents"]), 1)
        self.project.agents.create_version.side_effect = create
        completed = self.publish()
        self.assertEqual(completed["status"], "published")
        self.assertEqual([a["reused"] for a in completed["agents"]], [True, False])


if __name__ == "__main__":
    unittest.main()
