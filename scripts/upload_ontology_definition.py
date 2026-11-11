"""Upload a complete Fabric ontology definition and wait for completion."""

import argparse
import json
import time
from pathlib import Path
from uuid import UUID

import requests

from setup import FABRIC, headers


def upload_definition(workspace_id: UUID, ontology_id: UUID, definition: Path) -> None:
    scope = "https://api.fabric.microsoft.com/.default"
    payload = json.loads(definition.read_text(encoding="utf-8"))
    if not isinstance(payload["definition"]["parts"], list) or not payload["definition"]["parts"]:
        raise ValueError("Expected a complete definition with a non-empty parts list")

    url = f"{FABRIC}/workspaces/{workspace_id}/ontologies/{ontology_id}/updateDefinition"
    response = requests.post(
        url, json=payload, headers=headers(scope),
        timeout=120, allow_redirects=False,
    )
    if response.status_code not in (200, 202):
        raise SystemExit(f"Fabric upload failed (HTTP {response.status_code}): {response.text}")
    if response.status_code == 200:
        print(f"Ontology updated from {definition} (HTTP 200).")
        return

    operation = response.headers.get("Location", "")
    if not operation.startswith(f"{FABRIC}/operations/"):
        raise SystemExit(f"Invalid Fabric operation Location: {operation}")
    for _ in range(120):
        time.sleep(3)
        response = requests.get(
            operation, headers=headers(scope),
            timeout=120, allow_redirects=False,
        )
        if response.status_code != 200:
            raise SystemExit(f"Fabric operation failed (HTTP {response.status_code}): {response.text}")
        status = response.json().get("status")
        if status == "Succeeded":
            print(f"Ontology updated from {definition}.")
            return
        if status not in ("Running", "NotStarted"):
            raise SystemExit(f"Fabric operation failed: {response.text}")
    raise SystemExit(f"Timed out waiting for Fabric operation: {operation}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace_id", type=UUID)
    parser.add_argument("ontology_id", type=UUID)
    parser.add_argument("definition", type=Path)
    args = parser.parse_args()
    upload_definition(args.workspace_id, args.ontology_id, args.definition)


if __name__ == "__main__":
    main()
