"""Publish repository instructions while preserving the existing agent configuration."""

import argparse
import copy
import sys

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import PromptAgentDefinition
from azure.identity import AzureCliCredential

from setup_agent import INSTRUCTIONS


def update_instructions(endpoint: str, agent_name: str) -> str:
    instructions = INSTRUCTIONS.read_text(encoding="utf-8")
    if not instructions.strip():
        raise ValueError("Agent instructions must not be empty")
    with AIProjectClient(endpoint=endpoint, credential=AzureCliCredential()) as client:
        latest = next(iter(client.agents.list_versions(
            agent_name=agent_name, limit=1, order="desc", include_drafts=False,
        )), None)
        if latest is None:
            raise ValueError(f"No published version found for agent {agent_name}")
        if not isinstance(latest.definition, PromptAgentDefinition):
            raise ValueError(f"Agent {agent_name} is not a prompt agent")
        if latest.definition.instructions == instructions:
            return latest.version
        definition = copy.deepcopy(latest.definition)
        definition.instructions = instructions
        updated = client.agents.create_version(
            agent_name=agent_name, definition=definition,
            metadata=latest.metadata, description=latest.description,
            blueprint_reference=latest.blueprint_reference,
        )
        print(f"Published {agent_name} version {updated.version}.", file=sys.stderr)
        return updated.version


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("endpoint")
    parser.add_argument("agent_name")
    args = parser.parse_args()
    print(update_instructions(args.endpoint, args.agent_name))


if __name__ == "__main__":
    main()
