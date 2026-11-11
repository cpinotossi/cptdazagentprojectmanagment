import base64
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_synonym_definitions


class SynonymDefinitionTests(unittest.TestCase):
    def notebook_sources(self) -> dict[str, str]:
        notebook = json.loads((ROOT / "notebooks/project_synonym_test.ipynb").read_text())
        return {cell["id"]: "".join(cell["source"]) for cell in notebook["cells"]}

    def fake_definition(self, project: str) -> str:
        return json.dumps({"definition": {"parts": [{
            "path": "entities/Project.tmdl", "payloadType": "InlineBase64",
            "payload": base64.b64encode(project.encode()).decode(),
        }]}})

    def test_only_negative_control_removes_the_permanent_synonym(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ontology = Path(directory) / "ontology"
            (ontology / "entities").mkdir(parents=True)
            project = (ROOT / "ontology/entities/Project.tmdl").read_text()
            (ontology / "entities/Project.tmdl").write_text(project)
            (ontology / "model.tmdl").write_text("model Model\n")
            with patch.object(build_synonym_definitions, "ONTOLOGY_DIR", ontology), \
                    patch.object(sys, "argv", ["build", "workspace", "Workspace", "model", "Model"]):
                build_synonym_definitions.main()
            output = ontology.parent / "notebooks/fixtures/project_synonym"
            repository = json.loads((output / "fabric-ontology-repository.json").read_text())
            positive = json.loads((output / "fabric-ontology-qwert.json").read_text())
            negative = json.loads((output / "fabric-ontology-without-qwert.json").read_text())
            self.assertEqual(repository, positive)
            expected = copy.deepcopy(repository)
            part = next(p for p in expected["definition"]["parts"] if p["path"] == "entities/Project.tmdl")
            part["payload"] = base64.b64encode(project.replace("\tsynonym qwert\n", "").encode()).decode()
            self.assertEqual(negative, expected)
            self.assertEqual((output / "Project.tmdl").read_text(), project)

    def test_missing_permanent_synonym_fails_before_writing_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ontology = Path(directory) / "ontology"
            (ontology / "entities").mkdir(parents=True)
            (ontology / "entities/Project.tmdl").write_text("entity Project\n")
            with patch.object(build_synonym_definitions, "ONTOLOGY_DIR", ontology), \
                    patch.object(sys, "argv", ["build", "workspace", "Workspace", "model", "Model"]):
                with self.assertRaises(SystemExit) as error:
                    build_synonym_definitions.main()
            self.assertEqual(error.exception.code, 2)
            self.assertFalse((ontology.parent / "notebooks").exists())

    def test_notebook_uses_negative_control_and_restores_permanent_definition(self) -> None:
        notebook = json.loads((ROOT / "notebooks/project_synonym_test.ipynb").read_text())
        sources = {c["id"]: "".join(c["source"]) for c in notebook["cells"]}
        self.assertIn("fabric-ontology-without-qwert.json", sources["f46b06f6"])
        self.assertIn("fabric-ontology-repository.json", sources["2051225a"])
        self.assertIn("not", sources["28fdff23"])
        self.assertIn("synonym", sources["f79a8ad0"])
        self.assertIn("qwert", sources["f79a8ad0"])
        for cell in notebook["cells"]:
            if cell["cell_type"] == "code":
                with self.subTest(cell=cell["id"]):
                    result = subprocess.run(
                        ["bash", "-n"], input="".join(cell["source"]),
                        text=True, capture_output=True,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)

    def test_notebook_metadata_checks_reject_the_wrong_synonym_state(self) -> None:
        sources = self.notebook_sources()
        for cell_id, expected in (("28fdff23", False), ("ed99fcae", True)):
            for present in (False, True):
                with self.subTest(cell=cell_id, present=present):
                    project = "entity Project\n" + ("\tsynonym qwert\n" if present else "")
                    result = subprocess.run(
                        ["bash", "-c", 'az() { printf "%s\\n" "$FABRIC_DEFINITION"; };\n' + sources[cell_id]],
                        env=os.environ | {
                            "FABRIC_DEFINITION": self.fake_definition(project),
                            "ONTOLOGY_URL": "https://fabric.example/ontology",
                        },
                        capture_output=True, text=True,
                    )
                    self.assertEqual(result.returncode == 0, present == expected, result.stderr)
                    if present != expected:
                        self.assertIn("Unerwarteter qwert-Stand", result.stderr)

    def test_notebook_matrix_checks_actual_calls_arguments_and_records(self) -> None:
        def response(positive: bool) -> dict:
            answer = "P-100, P-200" if positive else "qwert ist unbekannt"
            return {
                "answer": [answer],
                "calls": [{
                    "name": "ask_ontology", "status": "completed",
                    "arguments": json.dumps({"request": "Liste alle qwert"}),
                    "result": json.dumps({"response": answer}),
                }],
            }

        valid = {
            "RES_ORIGINAL": response(False), "RES_WITHOUT_SYNONYM_HINTS": response(False),
            "RES_QWERT": response(True), "RES_WITH_HINTS": response(True),
        }
        cases = {"valid": valid}
        for name in ("missing_record", "wrong_tool", "rewritten_term", "invented_negative_records"):
            altered = copy.deepcopy(valid)
            if name == "missing_record":
                altered["RES_QWERT"]["answer"] = ["P-100"]
            elif name == "wrong_tool":
                altered["RES_QWERT"]["calls"][0]["name"] = "list_ontology_entities"
            elif name == "rewritten_term":
                altered["RES_QWERT"]["calls"][0]["arguments"] = '{"request":"Liste alle Projekte"}'
            else:
                altered["RES_ORIGINAL"] = response(True)
            cases[name] = altered
        for name, responses in cases.items():
            with self.subTest(case=name):
                result = subprocess.run(
                    ["bash", "-c", 'az() { printf "%s\\n" "$FABRIC_DEFINITION"; };\n'
                     + self.notebook_sources()["f79a8ad0"]],
                    env=os.environ | {key: json.dumps(value) for key, value in responses.items()} | {
                        "FABRIC_DEFINITION": self.fake_definition("entity Project\n\tsynonym qwert\n"),
                        "ONTOLOGY_URL": "https://fabric.example/ontology",
                    },
                    capture_output=True, text=True,
                )
                self.assertEqual(result.returncode == 0, name == "valid", result.stderr)


if __name__ == "__main__":
    unittest.main()
