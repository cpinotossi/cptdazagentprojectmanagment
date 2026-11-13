"""Import PDFs, CSV tables, semantic model or ontology independently."""

import argparse
import csv
import io
import json
from pathlib import Path

import setup
from semantic_refresh import SemanticModel, csv_rows, row_fingerprint


def existing_item(workspace_id: str | None, kind: str, name: str) -> str:
    path = f"/workspaces/{workspace_id}/{kind}" if workspace_id else "/workspaces"
    item = setup.find(path, name)
    if item is None:
        raise ValueError(f"{kind} item {name!r} not found; run deploy.sh for initial provisioning")
    return item["id"]


def merge_document_rows(existing: list[dict], updates: list[dict], columns: list[str]) -> bytes:
    def keyed(rows):
        result = {}
        for row in rows:
            if set(row) != set(columns) or not row.get("DocId"):
                raise ValueError("Document row has missing columns or an empty DocId")
            if row["DocId"] in result:
                raise ValueError(f"Duplicate document ID: {row['DocId']}")
            result[row["DocId"]] = row
        return result

    merged = keyed(existing)
    merged.update(keyed(updates))
    schema = setup.table_schema("documents")
    row_fingerprint(list(merged.values()), schema)
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=columns)
    writer.writeheader()
    for key in sorted(merged):
        row = dict(merged[key])
        for column, kind in schema.items():
            if row[column] is not None and row[column] != "" and kind == "dateTime":
                row[column] = str(row[column])[:10]
        writer.writerow(row)
    return output.getvalue().encode("utf-8")


def pdf_sources(model: SemanticModel, pdf: Path | None) -> dict[str, bytes]:
    if pdf is not None:
        if not pdf.is_file() or pdf.suffix.lower() != ".pdf" or "_" not in pdf.stem:
            raise ValueError("Select an existing PDF named <ProjectId>_<document-name>.pdf")
        # Snapshot after extraction so long CU calls do not unnecessarily age the merge baseline.
        selected = setup.documents_csv(setup.content_understanding(), [pdf])
        model.refresh()
        existing = model.rows("documents")
        columns, updates = csv_rows(selected)
        content = merge_document_rows(existing, updates, columns)
        print(f"Merged {pdf.stem}; preserved {len(existing) - sum(r['DocId'] == pdf.stem for r in existing)} other documents.")
    else:
        content = setup.documents_csv(setup.content_understanding())
    (setup.EXTRACTION_DIR / "outputs" / "documents.csv").write_bytes(content)
    return {"documents": content}


def run_import(target: str, pdf: Path | None = None, tables: list[str] | None = None) -> dict:
    workspace_id = existing_item(None, "workspaces", setup.WORKSPACE_NAME)
    lakehouse_id = existing_item(workspace_id, "lakehouses", setup.LAKEHOUSE_NAME)
    if target == "semantic":
        model_id = setup.import_semantic(workspace_id, lakehouse_id)
        verification = setup.refresh_and_verify(workspace_id, model_id, {})
        model = SemanticModel(workspace_id, model_id, setup.cred)
        counts = {}
        for table in setup.TABLES:
            rows = model.rows(table)
            row_fingerprint(rows, setup.table_schema(table))
            counts[table] = len(rows)
        return {"target": target, "model_id": model_id, **verification, "readable_rows": counts}

    model_id = existing_item(workspace_id, "semanticModels", setup.SEMANTIC_MODEL_NAME)
    if target == "ontology":
        ontology_id = setup.import_ontology(workspace_id, lakehouse_id, model_id)
        return {"target": target, "ontology_id": ontology_id}

    model = SemanticModel(workspace_id, model_id, setup.cred)
    if target == "pdfs":
        sources = pdf_sources(model, pdf)
    elif target == "csv":
        sources = setup.csv_sources(tables)
    else:
        raise ValueError(f"Unknown import target: {target}")
    setup.upload_tables(workspace_id, lakehouse_id, sources)
    verification = setup.refresh_and_verify(workspace_id, model_id, sources)
    return {"target": target, "model_id": model_id, "file": str(pdf) if pdf else None, **verification}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True, choices=["pdfs", "csv", "semantic", "ontology", "status"])
    parser.add_argument("--file", type=Path, help="Update only this PDF by DocId, preserving other live document rows")
    parser.add_argument("--table", action="append", help="CSV table to replace; repeat for several tables, or select a status table")
    parser.add_argument("--doc-id", help="Filter documents in status output")
    parser.add_argument("--report", type=Path, default=setup.EXTRACTION_DIR / "outputs" / "import_report.json")
    args = parser.parse_args()
    if args.file and args.target != "pdfs":
        parser.error("--file is only valid for --target pdfs")
    if args.table and args.target not in {"csv", "status"}:
        parser.error("--table is only valid for --target csv or status")
    if args.doc_id and args.target != "status":
        parser.error("--doc-id is only valid for --target status")
    if args.target == "status":
        if args.table and (len(args.table) != 1 or args.table[0] not in setup.TABLES):
            parser.error("status requires one known table")
        table = args.table[0] if args.table else "documents"
        if args.doc_id and table != "documents":
            parser.error("--doc-id requires the documents table")
        workspace = existing_item(None, "workspaces", setup.WORKSPACE_NAME)
        model_id = existing_item(workspace, "semanticModels", setup.SEMANTIC_MODEL_NAME)
        rows = SemanticModel(workspace, model_id, setup.cred).rows(table)
        if args.doc_id:
            rows = [row for row in rows if row["DocId"] == args.doc_id]
            if not rows:
                raise ValueError(f"Document {args.doc_id!r} is not visible in the semantic model")
        print(json.dumps({"table": table, "rows": rows}, ensure_ascii=False, indent=2))
        return
    result = run_import(args.target, args.file, args.table)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"Import completed and report saved to {args.report}")


if __name__ == "__main__":
    main()
