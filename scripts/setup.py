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

from content_understanding import ContentUnderstanding, field_value

DATA = Path(__file__).resolve().parent.parent / "data"
EXTRACTION_DIR = Path(__file__).resolve().parent.parent / "extraction"
DOCUMENT_ANALYZER = "projectDocumentTerms"
FABRIC = "https://api.fabric.microsoft.com/v1"
WORKSPACE_NAME = "ProjectPlanning"
LAKEHOUSE_NAME = "projectdata"
ONTOLOGY_DIR = Path(__file__).resolve().parent.parent / "ontology"
ONTOLOGY_NAME = json.loads((ONTOLOGY_DIR / ".platform").read_text())["metadata"]["displayName"]
TABLES = sorted(p.stem for p in (ONTOLOGY_DIR / "tables").glob("*.tmdl"))
SEMANTIC_MODEL_DIR = Path(__file__).resolve().parent.parent / "semantic_model"
SEMANTIC_MODEL_NAME = "ProjectPlanningModel"
AGENT_NAME = "project-planning-agent"
ONTOLOGY_CONNECTION = "fabric-iq-ontology"

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

def content_understanding():
    """Client for the Content Understanding of the Foundry account. Model name and deployment name are the same."""
    cu = ContentUnderstanding(f"https://{env('FOUNDRY_ACCOUNT_NAME')}.cognitiveservices.azure.com", cred)
    models = [env("FOUNDRY_MODEL_NAME"), env("FOUNDRY_EMBEDDING_NAME")]
    cu.set_defaults({m: m for m in models})
    return cu


def documents_csv(cu):
    """Turns the PDFs into a table with one row per document: the text plus the terms that Content Understanding extracts."""
    types = {"specification": "Specification", "offer": "Offer", "contract": "Contract"}
    definition = EXTRACTION_DIR / "document_terms.json"
    spec = json.loads(definition.read_text())["fieldSchema"]["fields"]
    cu.ensure_analyzer(DOCUMENT_ANALYZER, definition)
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["DocId", "ProjectId", "DocType", "Title", "Content"] + list(spec))
    extracted = {}
    for pdf in sorted((DATA / "pdfs").glob("*.pdf")):
        project_id, kind = pdf.stem.split("_")
        content = cu.analyze_file(DOCUMENT_ANALYZER, pdf)
        values = {t: field_value(content["fields"].get(t)) for t in spec}
        confidence = {t: content["fields"].get(t, {}).get("confidence") for t in spec if values[t] is not None}
        extracted[pdf.stem] = (values, confidence)
        title, _, body = content["markdown"].strip().partition("\n")
        w.writerow([pdf.stem, project_id, types[kind], title.lstrip("# ").strip(), re.sub(r"\s+", " ", body).strip()]
                   + ["" if values[t] is None else values[t] for t in spec])
    report_extraction(extracted)
    return out.getvalue().encode("utf-8")


def report_extraction(extracted):
    """Compares the extracted terms with the known values of the sample documents (extraction/expected_document_terms.json)."""
    expected = json.loads((EXTRACTION_DIR / "expected_document_terms.json").read_text())
    right = total = 0
    for doc, (values, confidence) in extracted.items():
        for term, value in values.items():
            total += 1
            if value == expected.get(doc, {}).get(term):
                right += 1
            else:
                print(f"  Extraction differs: {doc} {term}: expected {expected.get(doc, {}).get(term)}, got {value}")
    low = [f"{doc} {t} ({c:.2f})" for doc, (_, conf) in extracted.items() for t, c in conf.items() if c is not None and c < 0.6]
    print(f"  Extraction: {right} of {total} fields match the expected values")
    if low:
        print("  Low confidence (below 0.60): " + ", ".join(low))


def check_columns(table, header):
    """Stops early when the columns of a CSV, the ontology table file and the semantic model table file differ."""
    files = [f"ontology/tables/{table}.tmdl", f"semantic_model/definition/tables/{table}.tmdl"]
    for relative in files:
        expected = re.findall(r"(?m)^\tcolumn (\w+)$", (ONTOLOGY_DIR.parent / relative).read_text(encoding="utf-8"))
        if sorted(header) != sorted(expected):
            sys.exit(f"Table {table}: columns {sorted(set(header) ^ set(expected))} differ between the data and {relative}")


def upload_and_load(workspace_id, lakehouse_id):
    files = DataLakeServiceClient("https://onelake.dfs.fabric.microsoft.com", credential=cred).get_file_system_client(workspace_id)
    sources = {t: (DATA / f"{t}.csv").read_bytes() for t in TABLES if t != "documents"}
    sources["documents"] = documents_csv(content_understanding())
    for table, content in sources.items():
        check_columns(table, next(csv.reader(io.StringIO(content.decode("utf-8")))))
        files.get_file_client(f"{lakehouse_id}/Files/{table}.csv").upload_data(content, overwrite=True)
        fabric("POST", f"/workspaces/{workspace_id}/lakehouses/{lakehouse_id}/tables/{table}/load", json={
            "relativePath": f"Files/{table}.csv",
            "pathType": "File",
            "mode": "Overwrite",
            "formatOptions": {"format": "Csv", "header": True, "delimiter": ","},
        })
        print(f"  Loaded table {table}")


# ---------- Ontology (TMDL) ----------

def environment_values(ws_id, sql, model_id=""):
    """The values that differ per environment, for the placeholders in the TMDL files."""
    return {"{{SQL_SERVER}}": sql["connectionString"], "{{SQL_DATABASE_ID}}": sql["id"],
            "{{WORKSPACE_ID}}": ws_id, "{{WORKSPACE_NAME}}": WORKSPACE_NAME,
            "{{SEMANTIC_MODEL_ID}}": model_id, "{{SEMANTIC_MODEL_NAME}}": SEMANTIC_MODEL_NAME}


def definition_parts(folder, values):
    """Reads a TMDL folder and fills the environment specific placeholders."""
    parts = []
    for file in sorted(f for f in folder.rglob("*") if f.is_file()):
        text = file.read_text(encoding="utf-8")
        for placeholder, value in values.items():
            text = text.replace(placeholder, value)
        parts.append({"path": file.relative_to(folder).as_posix(),
                      "payload": base64.b64encode(text.encode()).decode(), "payloadType": "InlineBase64"})
    return parts


def ontology_parts(values):
    return definition_parts(ONTOLOGY_DIR, values)


def create_or_update(ws_id, kind, name, definition):
    """Creates a Fabric item (path segment kind, for example ontologies) or replaces its definition."""
    item = find(f"/workspaces/{ws_id}/{kind}", name)
    if item:
        fabric("POST", f"/workspaces/{ws_id}/{kind}/{item['id']}/updateDefinition", json={"definition": definition})
    else:
        fabric("POST", f"/workspaces/{ws_id}/{kind}", json={"displayName": name, "definition": definition})
        item = find(f"/workspaces/{ws_id}/{kind}", name)
    return item["id"]


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

    print("Fabric: creating the semantic model")
    parts = definition_parts(SEMANTIC_MODEL_DIR, environment_values(ws_id, sql))
    model_id = create_or_update(ws_id, "semanticModels", SEMANTIC_MODEL_NAME, {"parts": parts})

    print("Fabric: creating the ontology")
    ontology_id = create_or_update(ws_id, "ontologies", ONTOLOGY_NAME,
                                   {"parts": ontology_parts(environment_values(ws_id, sql, model_id))})
    return ws_id, ontology_id


def create_connection(name, target):
    sub, rg, account, project = env("AZURE_SUBSCRIPTION_ID"), env("AZURE_RESOURCE_GROUP"), env("FOUNDRY_ACCOUNT_NAME"), env("FOUNDRY_PROJECT_NAME")
    url = (f"https://management.azure.com/subscriptions/{sub}/resourceGroups/{rg}/providers/Microsoft.CognitiveServices/"
           f"accounts/{account}/projects/{project}/connections/{name}?api-version=2025-10-01-preview")
    body = {"properties": {
        "category": "RemoteTool",
        "authType": "UserEntraToken",
        "target": target,
        "audience": "https://analysis.windows.net/powerbi/api",
        "isSharedToAll": True,
    }}
    r = requests.put(url, headers=headers("https://management.azure.com/.default"), json=body)
    if not r.ok:
        sys.exit(f"Connection {name} failed: {r.status_code} {r.text}")
    return r.json()["id"]


def setup_agent(ws_id, ontology_id):
    print("Foundry: creating the Fabric IQ connection")
    connection = create_connection(
        ONTOLOGY_CONNECTION, f"https://api.fabric.microsoft.com/v1/mcp/dataPlane/workspaces/{ws_id}/items/{ontology_id}/ontologyEndpoint")

    print("Foundry: creating the agent")
    instructions = (DATA.parent / "scripts" / "agent_instructions.txt").read_text(encoding="utf-8")
    with AIProjectClient(endpoint=env("FOUNDRY_PROJECT_ENDPOINT"), credential=cred) as client:
        agent = client.agents.create_version(
            agent_name=AGENT_NAME,
            definition=PromptAgentDefinition(
                model=env("FOUNDRY_MODEL_NAME"),
                instructions=instructions,
                tools=[FabricIQPreviewTool(project_connection_id=connection, require_approval="never")],
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
