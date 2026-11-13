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
from urllib.parse import urlparse
from uuid import UUID

import requests
from azure.identity import AzureCliCredential
from azure.storage.filedatalake import DataLakeServiceClient

from content_understanding import ContentUnderstanding, field_value
from setup_agent import setup_agent
from semantic_refresh import SemanticModel, csv_rows

DATA = Path(__file__).resolve().parent.parent / "data"
EXTRACTION_DIR = Path(__file__).resolve().parent.parent / "extraction"
DOCUMENT_ANALYZER = "projectDocumentTerms"
DOCUMENT_ROUTER = "projectDocumentRouter"
FABRIC = "https://api.fabric.microsoft.com/v1"
WORKSPACE_NAME = "ProjectPlanning"
LAKEHOUSE_NAME = "projectdata"
ONTOLOGY_DIR = Path(__file__).resolve().parent.parent / "ontology"
ONTOLOGY_NAME = json.loads((ONTOLOGY_DIR / ".platform").read_text())["metadata"]["displayName"]
TABLES = sorted(p.stem for p in (ONTOLOGY_DIR / "tables").glob("*.tmdl"))
SEMANTIC_MODEL_DIR = Path(__file__).resolve().parent.parent / "semantic_model"
SEMANTIC_MODEL_NAME = "ProjectPlanningModel"

# The ontology itself (entities, descriptions, synonyms, relationships, rules, bindings) lives in
# ontology/ as TMDL files, the Fabric ontology definition format. Agents read the descriptions
# and rules at runtime, so the agent prompt does not repeat them.

cred = AzureCliCredential()


def headers(scope):
    return {"Authorization": f"Bearer {cred.get_token(scope).token}"}


def fabric(method, path, *, return_result=False, **kwargs):
    r = requests.request(method, FABRIC + path, headers=headers("https://api.fabric.microsoft.com/.default"), timeout=120, **kwargs)
    if r.status_code == 202:
        operation = wait_operation(r)
        if not return_result:
            return {}
        r = requests.get(operation + "/result", headers=headers("https://api.fabric.microsoft.com/.default"), timeout=120)
    if not r.ok:
        sys.exit(f"{method} {path} -> {r.status_code}: {r.text}")
    return r.json() if r.content else {}


def wait_operation(r, timeout=900):
    path = urlparse(r.headers["Location"]).path.rstrip("/")
    if "/operations/" not in path:
        raise ValueError(f"Unexpected Fabric operation URL: {r.headers['Location']}")
    operation_id = str(UUID(path.rsplit("/", 1)[-1]))
    url = f"{FABRIC}/operations/{operation_id}"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(int(r.headers.get("Retry-After", 3)))
        r = requests.get(url, headers=headers("https://api.fabric.microsoft.com/.default"), timeout=120)
        if not r.ok:
            sys.exit(f"Fabric operation HTTP {r.status_code}: {r.text}")
        status = r.json().get("status")
        if status == "Succeeded":
            return url
        if status not in {"Running", "NotStarted"}:
            sys.exit(f"Fabric operation failed: {r.text}")
    raise TimeoutError(f"Fabric operation still running: {url}")


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


def routed_document(result, filename):
    """Requires one supported, routed document instead of guessing from its filename."""
    contents = result.get("contents", [])
    if len(contents) != 1:
        raise ValueError(f"{filename}: expected one routed document, got {len(contents)}")
    content = contents[0]
    if content.get("category") not in {"Specification", "Offer", "Contract"}:
        raise ValueError(f"{filename}: unsupported CU document category {content.get('category')!r}")
    if content.get("analyzerId") != DOCUMENT_ANALYZER:
        raise ValueError(f"{filename}: expected routing to {DOCUMENT_ANALYZER}, got {content.get('analyzerId')!r}")
    if not isinstance(content.get("fields"), dict) or not isinstance(content.get("markdown"), str):
        raise ValueError(f"{filename}: routed document is missing fields or Markdown")
    return content


def documents_csv(cu, pdfs=None):
    """Turns the PDFs into a table with one row per document: the text plus the terms that Content Understanding extracts."""
    output_dir = EXTRACTION_DIR / "outputs"
    output_dir.mkdir(parents=True, exist_ok=True)
    definition = EXTRACTION_DIR / "document_terms.json"
    spec = json.loads(definition.read_text())["fieldSchema"]["fields"]
    cu.ensure_analyzer(DOCUMENT_ANALYZER, definition)
    cu.ensure_analyzer(DOCUMENT_ROUTER, EXTRACTION_DIR / "document_router.json")
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["DocId", "ProjectId", "DocType", "Title", "Content"] + list(spec))
    extracted = {}
    paths = sorted((DATA / "pdfs").glob("*.pdf")) if pdfs is None else pdfs
    if not paths:
        raise ValueError("No PDFs selected; refusing to replace the document table with an empty import")
    for pdf in paths:
        project_id = pdf.stem.split("_", 1)[0]
        result = cu.analyze_file(DOCUMENT_ROUTER, pdf)
        (output_dir / f"{pdf.stem}.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )
        content = routed_document(result, pdf.name)
        print(f"  Classified {pdf.name}: {content['category']}")
        values = {t: field_value(content["fields"].get(t)) for t in spec}
        confidence = {t: content["fields"].get(t, {}).get("confidence") for t in spec if values[t] is not None}
        extracted[pdf.stem] = (values, confidence)
        title, _, body = content["markdown"].strip().partition("\n")
        w.writerow([pdf.stem, project_id, content["category"], title.lstrip("# ").strip(), re.sub(r"\s+", " ", body).strip()]
                   + ["" if values[t] is None else values[t] for t in spec])
    report_extraction(extracted)
    csv_content = out.getvalue().encode("utf-8")
    csv_path = output_dir / "documents.csv"
    csv_path.write_bytes(csv_content)
    print(f"  Content Understanding outputs saved to {output_dir}")
    return csv_content


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


def csv_sources(tables=None):
    selected = [t for t in TABLES if t != "documents"] if tables is None else tables
    if (tables is not None and not selected) or any(t not in TABLES or t == "documents" for t in selected):
        raise ValueError(f"Expected CSV table names, excluding documents: {selected}")
    return {t: (DATA / f"{t}.csv").read_bytes() for t in selected}


def upload_tables(workspace_id, lakehouse_id, sources):
    for table, content in sources.items():
        if table not in TABLES:
            raise ValueError(f"Unsupported table: {table}")
        columns, _ = csv_rows(content)
        check_columns(table, columns)
    files = DataLakeServiceClient("https://onelake.dfs.fabric.microsoft.com", credential=cred).get_file_system_client(workspace_id)
    for table, content in sources.items():
        files.get_file_client(f"{lakehouse_id}/Files/{table}.csv").upload_data(content, overwrite=True)
        fabric("POST", f"/workspaces/{workspace_id}/lakehouses/{lakehouse_id}/tables/{table}/load", json={
            "relativePath": f"Files/{table}.csv",
            "pathType": "File",
            "mode": "Overwrite",
            "formatOptions": {"format": "Csv", "header": True, "delimiter": ","},
        })
        print(f"  Loaded table {table}")


def upload_and_load(workspace_id, lakehouse_id):
    sources = csv_sources()
    sources["documents"] = documents_csv(content_understanding())
    upload_tables(workspace_id, lakehouse_id, sources)
    return sources


def table_schema(table):
    if table not in TABLES:
        raise ValueError(f"Unsupported table: {table}")
    text = (SEMANTIC_MODEL_DIR / "definition" / "tables" / f"{table}.tmdl").read_text(encoding="utf-8")
    return dict(re.findall(r"(?m)^\tcolumn (\w+)\n\t\tdataType: (\w+)", text))


def wait_sql_endpoint(workspace_id, lakehouse_id, timeout=900):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        sql = fabric("GET", f"/workspaces/{workspace_id}/lakehouses/{lakehouse_id}")["properties"]["sqlEndpointProperties"]
        if sql.get("provisioningStatus") == "Success":
            return sql
        if sql.get("provisioningStatus") == "Failed":
            raise RuntimeError(f"SQL endpoint provisioning failed: {sql}")
        time.sleep(10)
    raise TimeoutError("SQL endpoint provisioning timed out")


def refresh_sql_schema(workspace_id, sql):
    result = fabric(
        "POST", f"/workspaces/{workspace_id}/sqlEndpoints/{sql['id']}/refreshMetadata",
        json={}, return_result=True,
    )
    statuses = result.get("value", [])
    if not statuses or any(
        item.get("error")
        or item.get("status") not in {"Success", "NotRun"}
        or (item.get("status") == "NotRun" and not item.get("lastSuccessfulSyncDateTime"))
        for item in statuses
    ):
        raise RuntimeError(f"SQL metadata synchronization failed or returned no statuses: {result}")
    for item in statuses:
        if item["status"] == "NotRun":
            print(
                f"SQL metadata: {item['tableName']} was not refreshed (NotRun); "
                f"last successful sync: {item['lastSuccessfulSyncDateTime']}. "
                "Model refresh and table verification are still required.",
                flush=True,
            )
    print("SQL metadata request completed; continuing with model refresh and verification.", flush=True)


def import_semantic(workspace_id, lakehouse_id):
    sql = wait_sql_endpoint(workspace_id, lakehouse_id)
    refresh_sql_schema(workspace_id, sql)
    parts = definition_parts(SEMANTIC_MODEL_DIR, environment_values(workspace_id, sql))
    return create_or_update(workspace_id, "semanticModels", SEMANTIC_MODEL_NAME, {"parts": parts})


def import_ontology(workspace_id, lakehouse_id, model_id):
    sql = wait_sql_endpoint(workspace_id, lakehouse_id)
    return create_or_update(
        workspace_id, "ontologies", ONTOLOGY_NAME,
        {"parts": ontology_parts(environment_values(workspace_id, sql, model_id))},
    )


def refresh_and_verify(workspace_id, model_id, sources):
    return SemanticModel(workspace_id, model_id, cred).refresh_and_verify(
        sources, {table: table_schema(table) for table in sources},
    )


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
    sources = upload_and_load(ws_id, lakehouse["id"])

    print("Fabric: creating the semantic model")
    model_id = import_semantic(ws_id, lakehouse["id"])
    refresh_and_verify(ws_id, model_id, sources)

    print("Fabric: creating the ontology")
    ontology_id = import_ontology(ws_id, lakehouse["id"], model_id)
    return ws_id, ontology_id


def main():
    workspace_id, ontology_id = setup_fabric()
    setup_agent(
        workspace_id, ontology_id,
        subscription_id=env("AZURE_SUBSCRIPTION_ID"),
        resource_group=env("AZURE_RESOURCE_GROUP"),
        account_name=env("FOUNDRY_ACCOUNT_NAME"),
        project_name=env("FOUNDRY_PROJECT_NAME"),
        endpoint=env("FOUNDRY_PROJECT_ENDPOINT"),
        model_name=env("FOUNDRY_MODEL_NAME"),
        credential=cred,
    )


if __name__ == "__main__":
    main()
