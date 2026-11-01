"""Richtet Fabric (Lakehouse, Ontology) und den Foundry-Agent ein. Idempotent, kann erneut ausgeführt werden."""
import base64
import csv
import io
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path

import requests
from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import FabricIQPreviewTool, PromptAgentDefinition
from azure.identity import AzureCliCredential
from azure.storage.filedatalake import DataLakeServiceClient
from pypdf import PdfReader

DATA = Path(__file__).resolve().parent.parent / "data"
FABRIC = "https://api.fabric.microsoft.com/v1"
WORKSPACE_NAME = "Projektplanung"
LAKEHOUSE_NAME = "projektdaten"
ONTOLOGY_NAME = "Projektplanung"
AGENT_NAME = "projektplanung-agent"
CONNECTION_NAME = "fabric-iq-ontology"

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
            sys.exit(f"Fabric-Operation fehlgeschlagen: {r.text}")


def find(path, name):
    return next((i for i in fabric("GET", path)["value"] if i["displayName"] == name), None)


def env(name):
    value = os.environ.get(name)
    if not value:
        sys.exit(f"Umgebungsvariable {name} fehlt (wird von deploy.sh gesetzt)")
    return value


# ---------- Daten ----------

def documents_csv():
    """Liest die PDFs und macht daraus eine Tabelle (eine Zeile pro Dokument)."""
    types = {"lastenheft": "Lastenheft", "angebot": "Angebot", "vertrag": "Vertrag"}
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["DocId", "ProjectId", "DocType", "Title", "Content"])
    for pdf in sorted((DATA / "pdfs").glob("*.pdf")):
        project_id, kind = pdf.stem.split("_")
        text = " ".join(p.extract_text() for p in PdfReader(pdf).pages)
        text = re.sub(r"\s+", " ", text).strip()
        title = text.split(" Auftraggeber")[0].split(" Anbieter")[0].split(" Vertragsparteien")[0]
        w.writerow([pdf.stem, project_id, types[kind], title, text])
    return out.getvalue().encode("utf-8")


TABLES = {
    # Tabelle: (Entität, Schlüssel, Beschreibung, Spalten)
    "project_managers": ("ProjectManager", "ManagerId", "Projektleiter", ["ManagerId", "Name", "Email"]),
    "projects": ("Project", "ProjectId", "Ein Kundenprojekt", ["ProjectId", "Name", "Customer", "ManagerId", "StartDate", "EndDate", "Budget"]),
    "documents": ("ProjectDocument", "DocId", "Lastenheft, Angebot oder Vertrag; Content enthält den PDF-Text", ["DocId", "ProjectId", "DocType", "Title", "Content"]),
    "emails": ("Email", "MailId", "Eingegangene E-Mail zu einem Projekt", ["MailId", "ProjectId", "Sender", "Subject", "Body", "ReceivedAt"]),
}
# (Name, von-Tabelle, nach-Tabelle, Fremdschlüssel, Schlüssel)
RELATIONS = [
    ("Project Managed By", "projects", "project_managers", "ManagerId"),
    ("Document Of Project", "documents", "projects", "ProjectId"),
    ("Email About Project", "emails", "projects", "ProjectId"),
]


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
        print(f"  Tabelle {table} geladen")


# ---------- Ontology (TMDL) ----------

def tag(*parts):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "/".join(parts)))


def ontology_parts(sql_server, sql_db_id):
    parts = {
        ".platform": json.dumps({
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json",
            "metadata": {"type": "Ontology", "displayName": ONTOLOGY_NAME},
            "config": {"version": "2.0", "logicalId": "00000000-0000-0000-0000-000000000000"},
        }),
        "database.tmdl": "database\n\tcompatibilityLevel: 1000000\n",
        "namespaces/default.tmdl": "namespace default\n\tlineageTag: default\n",
        "expressions.tmdl": (
            "expression DatabaseQuery =\n\t\tlet\n"
            f'\t\t    database = Sql.Database("{sql_server}", "{sql_db_id}")\n'
            "\t\tin\n\t\t    database\n"
            f"\tlineageTag: {tag('expr')}\n"
        ),
    }
    refs = []
    entity_by_table = {t: v[0] for t, v in TABLES.items()}
    for table, (entity, key, description, columns) in TABLES.items():
        t = [f"table {table}", f"\tlineageTag: {tag(table)}", ""]
        for c in columns:
            t += [f"\tcolumn {c}", "\t\tdataType: string", f"\t\tlineageTag: {tag(table, c)}", f"\t\tsourceColumn: {c}", ""]
        t += [f"\tpartition {table} = entity", "\t\tmode: directLake", "\t\tsource", f"\t\t\tentityName: {table}",
              "\t\t\tschemaName: dbo", "\t\t\texpressionSource: DatabaseQuery", ""]
        parts[f"tables/{table}.tmdl"] = "\n".join(t)

        e = [f"/// {description}", f"entity {entity}", f"\tlineageTag: {tag(entity)}", f"\tbackingTable: {table}", f"\tkeyProperty: {key}", ""]
        for c in columns:
            e += [f"\tproperty {c}", "\t\tdataType: string", f"\t\tlineageTag: {tag(entity, c)}", "",
                  "\t\tbackingConfiguration", f"\t\t\tvalueColumn: {table}.{c}", ""]
        parts[f"entities/{entity}.tmdl"] = "\n".join(e)
        refs.append(f"ref table {table}")
    refs += [""] + [f"ref entity {v}" for v in entity_by_table.values()] + ["", "ref namespace default"]
    parts["model.tmdl"] = "\n".join(["model Model", ""] + refs + [""])

    rels, entity_rels = [], []
    for name, source, target, fk in RELATIONS:
        rel_id = "rel_" + name.lower().replace(" ", "_")
        rels += [f"relationship {rel_id}", f"\tfromColumn: {source}.{fk}", f"\ttoColumn: {target}.{fk}", ""]
        entity_rels += [f"entityRelationship '{name}'", f"\tlineageTag: {tag(name)}", f"\tfromEntity: {entity_by_table[source]}",
                        f"\ttoEntity: {entity_by_table[target]}", "", "\tbackingConfiguration", f"\t\trelationship: {rel_id}", ""]
    parts["relationships.tmdl"] = "\n".join(rels)
    parts["entityRelationships.tmdl"] = "\n".join(entity_rels)
    return [{"path": p, "payload": base64.b64encode(c.encode()).decode(), "payloadType": "InlineBase64"} for p, c in parts.items()]


# ---------- Ablauf ----------

def setup_fabric():
    capacity_name = env("FABRIC_CAPACITY_NAME")
    print("Fabric: Kapazität suchen")
    capacity = None
    for _ in range(20):
        capacity = next((c for c in fabric("GET", "/capacities")["value"] if c["displayName"] == capacity_name), None)
        if capacity and capacity["state"] == "Active":
            break
        time.sleep(15)
    if not capacity:
        sys.exit(f"Kapazität {capacity_name} nicht gefunden (Deployer muss Kapazitätsadmin sein)")

    print("Fabric: Workspace und Lakehouse")
    ws = find("/workspaces", WORKSPACE_NAME)
    if not ws:
        ws = fabric("POST", "/workspaces", json={"displayName": WORKSPACE_NAME, "capacityId": capacity["id"]})
    ws_id = ws["id"]
    if not find(f"/workspaces/{ws_id}/lakehouses", LAKEHOUSE_NAME):
        fabric("POST", f"/workspaces/{ws_id}/lakehouses", json={"displayName": LAKEHOUSE_NAME})
    lakehouse = find(f"/workspaces/{ws_id}/lakehouses", LAKEHOUSE_NAME)

    print("Fabric: Daten hochladen und als Tabellen laden")
    upload_and_load(ws_id, lakehouse["id"])

    print("Fabric: SQL-Endpunkt abwarten")
    while True:
        sql = fabric("GET", f"/workspaces/{ws_id}/lakehouses/{lakehouse['id']}")["properties"]["sqlEndpointProperties"]
        if sql.get("provisioningStatus") == "Success":
            break
        time.sleep(10)

    print("Fabric: Ontology anlegen")
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
    print("Foundry: Fabric-IQ-Verbindung anlegen")
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
        sys.exit(f"Verbindung fehlgeschlagen: {r.status_code} {r.text}")

    print("Foundry: Agent anlegen")
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
        print(f"  Agent {agent.name} Version {agent.version}")
        with client.get_openai_client(agent_name=agent.name) as openai:
            question = "Welche E-Mails sind für den Projektverlauf kritisch? Bewerte sie und nenne den Projektleiter."
            print(f"\nTestfrage: {question}\n")
            print(openai.responses.create(input=question).output_text)


if __name__ == "__main__":
    workspace_id, ontology_id = setup_fabric()
    setup_agent(workspace_id, ontology_id)
