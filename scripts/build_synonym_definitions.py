"""Build the permanent ontology and a temporary negative control without qwert."""

import argparse
import base64
import copy
import json

from setup import ONTOLOGY_DIR, definition_parts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace_id")
    parser.add_argument("workspace_name")
    parser.add_argument("semantic_model_id")
    parser.add_argument("semantic_model_name")
    args = parser.parse_args()
    if not all(vars(args).values()):
        parser.error("Workspace and semantic model values must not be empty")

    values = {
        "{{WORKSPACE_ID}}": args.workspace_id,
        "{{WORKSPACE_NAME}}": args.workspace_name,
        "{{SEMANTIC_MODEL_ID}}": args.semantic_model_id,
        "{{SEMANTIC_MODEL_NAME}}": args.semantic_model_name,
    }
    parts = definition_parts(ONTOLOGY_DIR, values)
    if not parts:
        parser.error("No ontology files found")
    without_qwert = copy.deepcopy(parts)
    projects = [part for part in without_qwert if part["path"] == "entities/Project.tmdl"]
    if len(projects) != 1:
        parser.error("Expected exactly one Project entity")
    project = base64.b64decode(projects[0]["payload"]).decode("utf-8")
    synonym = "\tsynonym qwert\n"
    if project.count(synonym) != 1:
        parser.error("Repository Project must contain exactly one permanent synonym qwert")
    projects[0]["payload"] = base64.b64encode(project.replace(synonym, "").encode("utf-8")).decode()

    output = ONTOLOGY_DIR.parent / "notebooks" / "fixtures" / "project_synonym"
    output.mkdir(parents=True, exist_ok=True)
    (output / "Project.tmdl").write_text(project, encoding="utf-8")
    for name, definition in (
        ("fabric-ontology-repository.json", parts),
        ("fabric-ontology-qwert.json", parts),
        ("fabric-ontology-without-qwert.json", without_qwert),
    ):
        path = output / name
        path.write_text(
            json.dumps({"definition": {"parts": definition}}, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"Created {path}")


if __name__ == "__main__":
    main()
