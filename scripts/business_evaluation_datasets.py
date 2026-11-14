"""Publish immutable Foundry inputs and download pinned reference snapshots."""

import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

from azure.ai.projects import AIProjectClient
from azure.core.credentials import AzureSasCredential
from azure.core.exceptions import ResourceNotFoundError
from azure.storage.blob import BlobClient

from manual_agent_test import load_dataset
from foundry_datasets import jsonl_bytes

REFERENCE_NAME = "project-planning-business-reference"


def register_file(project: AIProjectClient, name: str, path: Path) -> dict:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    version = digest[:16]
    try:
        dataset = project.datasets.get(name=name, version=version)
    except ResourceNotFoundError:
        dataset = project.datasets.upload_file(
            name=name, version=version, file_path=str(path),
        )
    if not isinstance(dataset.id, str) or not dataset.id:
        raise ValueError("Registered Foundry dataset has no asset ID")
    print(f"Foundry dataset: {name} version {version}", flush=True)
    return {"id": dataset.id, "name": name, "version": version, "sha256": digest}


def publish_reference(project: AIProjectClient, path: Path) -> dict:
    load_dataset(path)
    return register_file(project, REFERENCE_NAME, path)


def download_reference(project: AIProjectClient, endpoint: str, manifest: Path) -> Path:
    asset = json.loads(manifest.read_text(encoding="utf-8"))
    if asset["project_endpoint"] != endpoint:
        raise ValueError("Reference asset belongs to a different Foundry project")
    dataset = project.datasets.get(name=asset["name"], version=asset["version"])
    if dataset.id != asset["id"]:
        raise ValueError("Foundry reference ID differs from the pinned asset")
    credentials = project.datasets.get_credentials(name=asset["name"], version=asset["version"])
    signature = urlsplit(credentials.blob_reference.credential.sas_uri).query
    if not signature:
        raise ValueError("Foundry reference credentials contain no SAS signature")
    with BlobClient.from_blob_url(
        dataset.data_uri, credential=AzureSasCredential(signature),
    ) as blob:
        content = blob.download_blob().readall()
    if hashlib.sha256(content).hexdigest() != asset["sha256"]:
        raise ValueError("Downloaded Foundry reference differs from its pinned hash")
    snapshot = manifest.with_suffix(".data.json")
    snapshot.write_bytes(content)
    load_dataset(snapshot)
    return snapshot


def register_evaluation_data(
    project: AIProjectClient, rows: list[dict], *, agent_names: list[str],
) -> dict:
    names = set(agent_names)
    suffix = next(iter(names)) if len(names) == 1 else "comparison" if names else "calibration"
    with TemporaryDirectory() as directory:
        path = Path(directory) / "inputs.jsonl"
        path.write_bytes(jsonl_bytes(rows))
        return register_file(project, f"project-planning-business-inputs-{suffix}", path)
