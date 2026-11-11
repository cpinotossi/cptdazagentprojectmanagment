import importlib.util
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from azure.ai.projects.models import FabricIQPreviewTool, PromptAgentDefinition

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import setup_agent


class AgentSetupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.credential = Mock()
        self.credential.get_token.return_value.token = "test-token"
        self.connection_args = {
            "subscription_id": "subscription", "resource_group": "group",
            "account_name": "account", "project_name": "project",
            "credential": self.credential,
        }

    def test_connection_preserves_endpoint_and_authentication(self) -> None:
        target = "https://fabric.example/ontologyEndpoint"
        with patch.object(setup_agent.requests, "put") as put:
            put.return_value.ok = True
            put.return_value.json.return_value = {"id": "connection-id"}
            result = setup_agent.create_connection("connection", target, **self.connection_args)
        self.assertEqual(result, "connection-id")
        self.credential.get_token.assert_called_once_with("https://management.azure.com/.default")
        put.assert_called_once_with(
            "https://management.azure.com/subscriptions/subscription/resourceGroups/group/"
            "providers/Microsoft.CognitiveServices/accounts/account/projects/project/"
            "connections/connection?api-version=2025-10-01-preview",
            headers={"Authorization": "Bearer test-token"},
            json={"properties": {
                "category": "RemoteTool", "authType": "UserEntraToken", "target": target,
                "audience": "https://analysis.windows.net/powerbi/api", "isSharedToAll": True,
            }},
        )

    def test_agent_definition_is_preserved_without_inference(self) -> None:
        with patch.object(setup_agent, "create_connection", return_value="connection-id") as connection, \
                patch.object(setup_agent, "AIProjectClient") as factory:
            client = factory.return_value.__enter__.return_value
            client.agents.create_version.return_value = SimpleNamespace(
                name="project-planning-agent", version="8",
            )
            setup_agent.setup_agent(
                "workspace", "ontology", endpoint="https://foundry.example",
                model_name="existing-model", **self.connection_args,
            )
        connection.assert_called_once_with(
            "fabric-iq-ontology",
            "https://api.fabric.microsoft.com/v1/mcp/dataPlane/workspaces/workspace/items/ontology/ontologyEndpoint",
            **self.connection_args,
        )
        factory.assert_called_once_with(
            endpoint="https://foundry.example", credential=self.credential,
        )
        kwargs = client.agents.create_version.call_args.kwargs
        self.assertEqual(kwargs["agent_name"], "project-planning-agent")
        expected = PromptAgentDefinition(
            model="existing-model",
            instructions=setup_agent.INSTRUCTIONS.read_text(encoding="utf-8"),
            tools=[FabricIQPreviewTool(project_connection_id="connection-id", require_approval="never")],
        )
        self.assertEqual(kwargs["definition"].as_dict(), expected.as_dict())
        client.agents.create_version.assert_called_once()
        client.get_openai_client.assert_not_called()

    def test_connection_failure_stops_before_agent_creation(self) -> None:
        with patch.object(setup_agent.requests, "put") as put, \
                patch.object(setup_agent, "AIProjectClient") as factory:
            put.return_value.ok = False
            put.return_value.status_code = 403
            put.return_value.text = "Forbidden"
            with self.assertRaisesRegex(SystemExit, "Connection fabric-iq-ontology failed: 403 Forbidden"):
                setup_agent.setup_agent(
                    "workspace", "ontology", endpoint="https://foundry.example",
                    model_name="existing-model", **self.connection_args,
                )
            factory.assert_not_called()

    def test_setup_passes_fabric_ids_and_existing_environment(self) -> None:
        spec = importlib.util.spec_from_file_location("project_setup", ROOT / "scripts/setup.py")
        assert spec is not None and spec.loader is not None
        setup = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(setup)
        environment = {
            "AZURE_SUBSCRIPTION_ID": "subscription", "AZURE_RESOURCE_GROUP": "group",
            "FOUNDRY_ACCOUNT_NAME": "account", "FOUNDRY_PROJECT_NAME": "project",
            "FOUNDRY_PROJECT_ENDPOINT": "https://foundry.example",
            "FOUNDRY_MODEL_NAME": "existing-model",
        }
        with patch.dict(os.environ, environment), \
                patch.object(setup, "setup_fabric", return_value=("workspace", "ontology")) as fabric, \
                patch.object(setup, "setup_agent") as agent:
            calls = Mock()
            calls.attach_mock(fabric, "fabric")
            calls.attach_mock(agent, "agent")
            setup.main()
            self.assertEqual([call[0] for call in calls.mock_calls], ["fabric", "agent"])
            agent.assert_called_once_with(
                "workspace", "ontology", **self.connection_args | {
                    "credential": setup.cred, "endpoint": "https://foundry.example",
                    "model_name": "existing-model",
                },
            )


if __name__ == "__main__":
    unittest.main()
