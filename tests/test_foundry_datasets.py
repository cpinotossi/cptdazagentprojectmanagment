import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import foundry_datasets as datasets


class FoundryDatasetCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.case = {
            "id": "B-001", "query": "Question", "context": "Evidence", "ground_truth": "Expected",
            "criteria": [{"id": "fact", "description": "Required fact"}],
            "critical_errors": [{"id": "wrong", "description": "Wrong fact"}],
        }
        self.source = self.root / "cases.json"
        self.source.write_text(json.dumps({"test_cases": [self.case]}))
        self.catalog = self.root / "datasets.json"
        self.entry = {
            "name": "cases", "kind": "cases", "sources": ["cases.json"], "output": "cases.jsonl",
        }
        self.write_catalog([self.entry])

    def write_catalog(self, entries: list[dict]) -> None:
        self.catalog.write_text(json.dumps({"schema_version": 1, "datasets": entries}))

    def test_multiple_sources_are_deterministic_and_duplicate_ids_are_rejected(self) -> None:
        other = self.root / "other.json"
        other.write_text(json.dumps({"test_cases": [self.case | {"id": "B-002"}]}))
        entry = self.entry | {"sources": ["other.json", "cases.json"]}
        self.write_catalog([entry])
        first = datasets.render_datasets(self.catalog)[0]
        self.assertEqual([json.loads(line)["id"] for line in first["content"].splitlines()],
                         ["B-001", "B-002"])
        self.write_catalog([entry | {"sources": ["cases.json", "other.json"]}])
        self.assertEqual(first["content"], datasets.render_datasets(self.catalog)[0]["content"])
        other.write_text(json.dumps({"test_cases": [self.case]}))
        with self.assertRaisesRegex(ValueError, "unique row IDs"):
            datasets.render_datasets(self.catalog)

    def test_calibration_joins_current_case_without_sending_labels_to_judge(self) -> None:
        controls = self.root / "controls.json"
        controls.write_text(json.dumps({
            "case_id": "B-001", "controls": [
                {"id": "good", "response": "Answer", "expected_pass": True},
            ],
        }))
        self.write_catalog([self.entry | {
            "kind": "calibration", "reference": "cases.json", "sources": ["controls.json"],
        }])
        item = datasets.render_datasets(self.catalog)[0]
        row = json.loads(item["content"])
        self.assertEqual(row["context"], "Evidence")
        self.assertEqual(json.loads(row["criteria"]), self.case["criteria"])
        self.assertEqual(row["response"], "Answer")
        self.assertNotIn("expected_pass", row)
        changed = copy.deepcopy(self.case)
        changed["context"] = "Updated reference"
        self.source.write_text(json.dumps({"test_cases": [changed]}))
        self.assertNotEqual(item["sha256"], datasets.render_datasets(self.catalog)[0]["sha256"])
        controls.write_text(json.dumps({"case_id": "missing", "controls": []}))
        with self.assertRaisesRegex(ValueError, "unknown reference"):
            datasets.render_datasets(self.catalog)

    def test_invalid_paths_names_and_outputs_fail_before_any_write(self) -> None:
        for entries in (
            [self.entry, self.entry],
            [self.entry | {"output": "../outside.jsonl"}],
            [self.entry | {"sources": ["../outside.json"]}],
            [self.entry | {"kind": "unknown"}],
            [self.entry | {"output": "cases.json"}],
        ):
            with self.subTest(entries=entries):
                self.write_catalog(entries)
                with self.assertRaises(ValueError):
                    datasets.render_datasets(self.catalog)
        self.assertFalse((self.root / "cases.jsonl").exists())

    def test_reference_is_portal_compatible_and_round_trips_without_losing_criteria(self) -> None:
        from manual_agent_test import load_dataset

        item = datasets.render_datasets(self.catalog)[0]
        item["path"].write_bytes(item["content"])
        row = json.loads(item["content"])
        self.assertIsInstance(row["criteria"], str)
        self.assertIsInstance(row["critical_errors"], str)
        restored, digest = load_dataset(item["path"])
        self.assertEqual(restored, [self.case])
        self.assertEqual(digest, item["sha256"])
        self.assertNotIn("response", row)

    def test_notebook_and_catalog_publication_use_the_same_checked_file(self) -> None:
        from business_evaluation_datasets import publish_reference

        project = Mock()
        with patch("business_evaluation_datasets.register_file") as register:
            publish_reference(project, datasets.REFERENCE_SOURCE)
        register.assert_called_once_with(project, datasets.REFERENCE_NAME, datasets.REFERENCE_PATH)
        with self.assertRaisesRegex(ValueError, "canonical reference"):
            publish_reference(project, self.source)

    def test_stale_committed_jsonl_blocks_upload_before_cloud_calls(self) -> None:
        items = datasets.render_datasets(self.catalog)
        project = Mock()
        with self.assertRaisesRegex(ValueError, "stale JSONL"):
            datasets.publish_datasets(project, items, self.root / "release.json", "endpoint")
        project.datasets.get.assert_not_called()
        items[0]["path"].write_bytes(items[0]["content"])
        datasets.check_datasets(items)
        self.source.write_text(json.dumps({"test_cases": [self.case | {"query": "Changed"}]}))
        with self.assertRaisesRegex(ValueError, "stale JSONL"):
            datasets.check_datasets(datasets.render_datasets(self.catalog))

    def test_upload_uses_shared_registration_and_records_verified_version(self) -> None:
        items = datasets.render_datasets(self.catalog)
        items[0]["path"].write_bytes(items[0]["content"])
        digest = items[0]["sha256"]
        asset = {"id": "asset-id", "name": "cases", "version": digest[:16], "sha256": digest}
        project = Mock()
        project.datasets.get.return_value = SimpleNamespace(id="asset-id")
        manifest = self.root / "release.json"
        with patch("business_evaluation_datasets.register_file", return_value=asset) as register:
            result = datasets.publish_datasets(project, items, manifest, "endpoint")
        register.assert_called_once_with(project, "cases", items[0]["path"])
        self.assertEqual(result["status"], "published")
        self.assertEqual(json.loads(manifest.read_text())["datasets"][0]["version"], digest[:16])

    def test_output_policy_keeps_notebooks_and_generated_jsonl_but_ignores_run_files(self) -> None:
        def ignored(path: str) -> bool:
            result = subprocess.run(
                ["git", "check-ignore", "--no-index", "-q", path], cwd=ROOT, check=False,
            )
            self.assertIn(result.returncode, (0, 1))
            return result.returncode == 0

        for path in ("evaluations/runs/new.json", "evaluations/notebook_runs/review.json",
                     "evaluations/archive/runs/old.json", "evaluations/releases/datasets.json"):
            self.assertTrue(ignored(path), path)
        for path in ("notebooks/business_all_tests.ipynb", "evaluations/business_tests.json",
                     "evaluations/generated/business_reference.jsonl"):
            self.assertFalse(ignored(path), path)


if __name__ == "__main__":
    unittest.main()
