"""Sets up Fabric (lakehouse, ontology) and the Foundry agent. Idempotent, safe to run again."""
import base64
import csv
import io
import json
import os
import re
import sys
import time
from pathlib import Path

import requests
from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import FabricIQPreviewTool, PromptAgentDefinition
from azure.identity import AzureCliCredential
from azure.storage.filedatalake import DataLakeServiceClient
from pypdf import PdfReader

DATA = Path(__file__).resolve().parent.parent / "data"
FABRIC = "https://api.fabric.microsoft.com/v1"
WORKSPACE_NAME = "ProjectPlanning"
LAKEHOUSE_NAME = "projectdata"
ONTOLOGY_DIR = Path(__file__).resolve().parent.parent / "ontology"
ONTOLOGY_NAME = json.loads((ONTOLOGY_DIR / ".platform").read_text())["metadata"]["displayName"]
TABLES = sorted(p.stem for p in (ONTOLOGY_DIR / "tables").glob("*.tmdl"))
AGENT_NAME = "project-planning-agent"
CONNECTION_NAME = "fabric-iq-ontology"

# The ontology itself (entities, descriptions, synonyms, relationships, rules, bindings) lives in
# ontology/ as TMDL files, the Fabric ontology definition format. Agents read the descriptions
# and rules at runtime, so the agent prompt does not repeat them.

cred = AzureCliCredential()


def headers(scope):
    return {"Authorization": f"Bearer {cred.get_token(scope).token}"}


def fabric(method, path, **kwargs):
    r = requests.request(method, FABRIC + path, headers=headers("https://api.fabric.microsoft.com/.default"), **kwargs)
    if r.status_code == 202:
        wait_operation(r)
        return {}
    if not r.ok:
        sys.exit(f"{method} {path} -> {r.status_code}: {r.text}")
    return r.json() if r.content else {}


def wait_operation(r):
    url = r.headers["Location"]
    while True:
        time.sleep(int(r.headers.get("Retry-After", 3)))
        r = requests.get(url, headers=headers("https://api.fabric.microsoft.com/.default"))
        status = r.json().get("status")
        if status == "Succeeded":
            return
        if status == "Failed":
            sys.exit(f"Fabric operation failed: {r.text}")


def find(path, name):
    return next((i for i in fabric("GET", path)["value"] if i["displayName"] == name), None)


def env(name):
    value = os.environ.get(name)
    if not value:
        sys.exit(f"Environment variable {name} is missing (set by deploy.sh)")
    return value


# ---------- Data ----------

def documents_csv():
    """Turns the PDFs into a table with one row per document."""
    types = {"specification": "Specification", "offer": "Offer", "contract": "Contract"}
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["DocId", "ProjectId", "DocType", "Title", "Content"])
    for pdf in sorted((DATA / "pdfs").glob("*.pdf")):
        project_id, kind = pdf.stem.split("_")
        title, _, body = PdfReader(pdf).pages[0].extract_text().strip().partition("\n")
        w.writerow([pdf.stem, project_id, types[kind], title.strip(), re.sub(r"\s+", " ", body).strip()])
    return out.getvalue().encode("utf-8")


def upload_and_load(workspace_id, lakehouse_id):
    files = DataLakeServiceClient("https://onelake.dfs.fabric.microsoft.com", credential=cred).get_file_system_client(workspace_id)
    sources = {t: (DATA / f"{t}.csv").read_bytes() for t in TABLES if t != "documents"}
    sources["documents"] = documents_csv()
    for table, content in sources.items():
        files.get_file_client(f"{lakehouse_id}/Files/{table}.csv").upload_data(content, overwrite=True)
        fabric("POST", f"/workspaces/{workspace_id}/lakehouses/{lakehouse_id}/tables/{table}/load", json={
            "relativePath": f"Files/{table}.csv",
            "pathType": "File",
            "mode": "Overwrite",
            "formatOptions": {"format": "Csv", "header": True, "delimiter": ","},
        })
        print(f"  Loaded table {table}")


# ---------- Ontology (TMDL) ----------

def ontology_parts(sql_server, sql_db_id):
    """Reads ontology/ and fills the two environment specific values of the SQL endpoint."""
    values = {"{{SQL_SERVER}}": sql_server, "{{SQL_DATABASE_ID}}": sql_db_id}
    parts = []
    for file in sorted(f for f in ONTOLOGY_DIR.rglob("*") if f.is_file()):
        text = file.read_text(encoding="utf-8")
        for placeholder, value in values.items():
            text = text.replace(placeholder, value)
        parts.append({"path": file.relative_to(ONTOLOGY_DIR).as_posix(),
                      "payload": base64.b64encode(text.encode()).decode(), "payloadType": "InlineBase64"})
    return parts


# ---------- Flow ----------

def setup_fabric():
    capacity_name = env("FABRIC_CAPACITY_NAME")
    print("Fabric: looking up capacity")
    capacity = None
    for _ in range(20):
        capacity = next((c for c in fabric("GET", "/capacities")["value"] if c["displayName"] == capacity_name), None)
        if capacity and capacity["state"] == "Active":
            break
        time.sleep(15)
    if not capacity:
        sys.exit(f"Capacity {capacity_name} not found (the deployer must be capacity admin)")

    print("Fabric: workspace and lakehouse")
    ws = find("/workspaces", WORKSPACE_NAME)
    if not ws:
        ws = fabric("POST", "/workspaces", json={"displayName": WORKSPACE_NAME, "capacityId": capacity["id"]})
    ws_id = ws["id"]
    if not find(f"/workspaces/{ws_id}/lakehouses", LAKEHOUSE_NAME):
        fabric("POST", f"/workspaces/{ws_id}/lakehouses", json={"displayName": LAKEHOUSE_NAME})
    lakehouse = find(f"/workspaces/{ws_id}/lakehouses", LAKEHOUSE_NAME)

    print("Fabric: uploading data and loading tables")
    upload_and_load(ws_id, lakehouse["id"])

    print("Fabric: waiting for the SQL endpoint")
    while True:
        sql = fabric("GET", f"/workspaces/{ws_id}/lakehouses/{lakehouse['id']}")["properties"]["sqlEndpointProperties"]
        if sql.get("provisioningStatus") == "Success":
            break
        time.sleep(10)

    print("Fabric: creating the ontology")
    definition = {"parts": ontology_parts(sql["connectionString"], sql["id"])}
    ontology = find(f"/workspaces/{ws_id}/ontologies", ONTOLOGY_NAME)
    if ontology:
        fabric("POST", f"/workspaces/{ws_id}/ontologies/{ontology['id']}/updateDefinition", json={"definition": definition})
    else:
        fabric("POST", f"/workspaces/{ws_id}/ontologies", json={"displayName": ONTOLOGY_NAME, "definition": definition})
        ontology = find(f"/workspaces/{ws_id}/ontologies", ONTOLOGY_NAME)
    return ws_id, ontology["id"]


def setup_agent(ws_id, ontology_id):
    sub, rg, account, project = env("AZURE_SUBSCRIPTION_ID"), env("AZURE_RESOURCE_GROUP"), env("FOUNDRY_ACCOUNT_NAME"), env("FOUNDRY_PROJECT_NAME")
    print("Foundry: creating the Fabric IQ connection")
    url = (f"https://management.azure.com/subscriptions/{sub}/resourceGroups/{rg}/providers/Microsoft.CognitiveServices/"
           f"accounts/{account}/projects/{project}/connections/{CONNECTION_NAME}?api-version=2025-10-01-preview")
    body = {"properties": {
        "category": "RemoteTool",
        "authType": "UserEntraToken",
        "target": f"https://api.fabric.microsoft.com/v1/mcp/dataPlane/workspaces/{ws_id}/items/{ontology_id}/ontologyEndpoint",
        "audience": "https://analysis.windows.net/powerbi/api",
        "isSharedToAll": True,
    }}
    r = requests.put(url, headers=headers("https://management.azure.com/.default"), json=body)
    if not r.ok:
        sys.exit(f"Connection failed: {r.status_code} {r.text}")

    print("Foundry: creating the agent")
    instructions = (DATA.parent / "scripts" / "agent_instructions.txt").read_text(encoding="utf-8")
    with AIProjectClient(endpoint=env("FOUNDRY_PROJECT_ENDPOINT"), credential=cred) as client:
        agent = client.agents.create_version(
            agent_name=AGENT_NAME,
            definition=PromptAgentDefinition(
                model=env("FOUNDRY_MODEL_NAME"),
                instructions=instructions,
                tools=[FabricIQPreviewTool(project_connection_id=r.json()["id"], require_approval="never")],
            ),
        )
        print(f"  Agent {agent.name} version {agent.version}")
        with client.get_openai_client(agent_name=agent.name) as openai:
            question = "Which emails are critical for the project? Rate each one and name the project manager."
            print(f"\nTest question: {question}\n")
            print(openai.responses.create(input=question).output_text)


if __name__ == "__main__":
    workspace_id, ontology_id = setup_fabric()
    setup_agent(workspace_id, ontology_id)
