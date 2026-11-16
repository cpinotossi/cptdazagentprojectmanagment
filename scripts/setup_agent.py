"""Create the Fabric IQ connection and Foundry agent for an existing ontology."""

import sys
from pathlib import Path

import requests
from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import FabricIQPreviewTool, PromptAgentDefinition
from azure.core.credentials import TokenCredential

AGENT_NAME = "project-planning-agent"
ONTOLOGY_CONNECTION = "fabric-iq-ontology"
INSTRUCTIONS = Path(__file__).resolve().parents[1] / "agents/fabric-iq/instructions.txt"
AGENT_SETTINGS: dict = {}


def agent_definition(model_name: str, connection: str) -> PromptAgentDefinition:
    """Build the same committed prompt/tool/settings intent for setup and release."""
    instructions = INSTRUCTIONS.read_text(encoding="utf-8")
    if not instructions.strip() or not model_name.strip() or not connection.strip():
        raise ValueError("Agent instructions, model deployment and connection must not be empty")
    if set(AGENT_SETTINGS) - {"temperature", "top_p", "reasoning", "text", "rai_config"}:
        raise ValueError("AGENT_SETTINGS must contain only supported prompt runtime settings")
    return PromptAgentDefinition({
        "kind": "prompt",
        **AGENT_SETTINGS,
        "model": model_name,
        "instructions": instructions,
        "tools": [FabricIQPreviewTool(project_connection_id=connection, require_approval="never").as_dict()],
    })


def create_connection(
    name: str, target: str, *, subscription_id: str, resource_group: str,
    account_name: str, project_name: str, credential: TokenCredential,
) -> str:
    url = (f"https://management.azure.com/subscriptions/{subscription_id}/resourceGroups/{resource_group}/"
           f"providers/Microsoft.CognitiveServices/accounts/{account_name}/projects/{project_name}/"
           f"connections/{name}?api-version=2025-10-01-preview")
    body = {"properties": {
        "category": "RemoteTool",
        "authType": "UserEntraToken",
        "target": target,
        "audience": "https://analysis.windows.net/powerbi/api",
        "isSharedToAll": True,
    }}
    token = credential.get_token("https://management.azure.com/.default").token
    r = requests.put(url, headers={"Authorization": f"Bearer {token}"}, json=body)
    if not r.ok:
        sys.exit(f"Connection {name} failed: {r.status_code} {r.text}")
    return r.json()["id"]


def setup_agent(
    ws_id: str, ontology_id: str, *, subscription_id: str, resource_group: str,
    account_name: str, project_name: str, endpoint: str, model_name: str,
    credential: TokenCredential,
) -> None:
    print("Foundry: creating the Fabric IQ connection")
    connection = create_connection(
        ONTOLOGY_CONNECTION,
        f"https://api.fabric.microsoft.com/v1/mcp/dataPlane/workspaces/{ws_id}/items/{ontology_id}/ontologyEndpoint",
        subscription_id=subscription_id, resource_group=resource_group,
        account_name=account_name, project_name=project_name, credential=credential,
    )

    print("Foundry: creating the agent")
    with AIProjectClient(endpoint=endpoint, credential=credential) as client:
        agent = client.agents.create_version(
            agent_name=AGENT_NAME,
            definition=agent_definition(model_name, connection),
        )
        print(f"  Agent {agent.name} version {agent.version}")
