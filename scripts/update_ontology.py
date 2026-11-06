"""Uploads the ontology/ folder to the existing Fabric ontology.

Without arguments it restores the files as they are. With --rule and --statement it replaces the
statement of one rule for the upload only, the files stay unchanged. The notebook uses this for
its live test of a rule.
"""
import argparse
import base64
import re
import sys

import setup


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rule", help="name of the rule whose statement is replaced")
    parser.add_argument("--statement", help="replacement statement")
    args = parser.parse_args()

    ws = setup.find("/workspaces", setup.WORKSPACE_NAME)["id"]
    lakehouse = setup.find(f"/workspaces/{ws}/lakehouses", setup.LAKEHOUSE_NAME)["id"]
    sql = setup.fabric("GET", f"/workspaces/{ws}/lakehouses/{lakehouse}")["properties"]["sqlEndpointProperties"]
    ontology = setup.find(f"/workspaces/{ws}/ontologies", setup.ONTOLOGY_NAME)["id"]
    model = setup.find(f"/workspaces/{ws}/semanticModels", setup.SEMANTIC_MODEL_NAME)["id"]

    parts = setup.ontology_parts(setup.environment_values(ws, sql, model))
    if args.rule:
        path = f"rules/{args.rule}.tmdl"
        part = next((p for p in parts if p["path"] == path), None)
        if part is None:
            sys.exit(f"No rule file {path}")
        text = base64.b64decode(part["payload"]).decode()
        text = re.sub(r"(?m)^(\tstatement: ).*$", lambda m: m.group(1) + args.statement, text)
        part["payload"] = base64.b64encode(text.encode()).decode()

    setup.fabric("POST", f"/workspaces/{ws}/ontologies/{ontology}/updateDefinition", json={"definition": {"parts": parts}})
    print(f"Ontology updated ({'rule ' + args.rule + ' overridden' if args.rule else 'files unchanged'})")


if __name__ == "__main__":
    main()
