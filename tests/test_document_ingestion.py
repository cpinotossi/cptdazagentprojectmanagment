import csv
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("document_ingestion_setup", ROOT / "scripts/setup.py")
assert spec is not None and spec.loader is not None
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class DocumentIngestionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.data = root / "data"
        self.extraction = root / "extraction"
        (self.data / "pdfs").mkdir(parents=True)
        self.extraction.mkdir()
        for name in ("P-100_offer", "P-200_contract"):
            (self.data / "pdfs" / f"{name}.pdf").write_bytes(b"test PDF")
        (self.extraction / "document_terms.json").write_text(json.dumps({
            "fieldSchema": {"fields": {"FixedPriceEur": {}, "AcceptanceDeadline": {}}},
        }), encoding="utf-8")
        (self.extraction / "expected_document_terms.json").write_text(json.dumps({
            "P-100_offer": {"FixedPriceEur": 2400000},
            "P-200_contract": {"AcceptanceDeadline": "2028-01-15"},
        }), encoding="utf-8")
        self.contents = {
            "P-100_offer": {
                "analyzerId": setup.DOCUMENT_ANALYZER,
                "category": "Offer",
                "markdown": "# Angebot Bürogebäude\nFestpreis: EUR 2.400.000 netto.\n",
                "fields": {"FixedPriceEur": {"valueNumber": 2400000, "confidence": 0.97}},
            },
            "P-200_contract": {
                "analyzerId": setup.DOCUMENT_ANALYZER,
                "category": "Contract",
                "markdown": "# Contract\nAcceptance: 2028-01-15.\n",
                "fields": {"AcceptanceDeadline": {"valueDate": "2028-01-15", "confidence": 0.95}},
            },
        }
        self.results = {
            name: {"analyzerId": setup.DOCUMENT_ROUTER, "contents": [content]}
            for name, content in self.contents.items()
        }
        self.cu = Mock()
        self.cu.analyze_file.side_effect = lambda analyzer, pdf: self.results[pdf.stem]
        self.outputs = self.extraction / "outputs"
        for name, value in (("DATA", self.data), ("EXTRACTION_DIR", self.extraction)):
            patcher = patch.object(setup, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_saves_analyzed_content_and_exact_csv_bytes_and_replaces_previous_outputs(self) -> None:
        self.outputs.mkdir()
        (self.outputs / "P-100_offer.json").write_text("stale", encoding="utf-8")
        (self.outputs / "documents.csv").write_bytes(b"stale")

        result = setup.documents_csv(self.cu)

        for name, content in self.results.items():
            saved = json.loads((self.outputs / f"{name}.json").read_text(encoding="utf-8"))
            self.assertEqual(saved, content)
        self.assertEqual((self.outputs / "documents.csv").read_bytes(), result)
        rows = list(csv.DictReader(io.StringIO(result.decode("utf-8"))))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["ProjectId"], "P-100")
        self.assertEqual(rows[0]["Title"], "Angebot Bürogebäude")
        self.assertEqual(rows[0]["FixedPriceEur"], "2400000")
        self.assertEqual(rows[0]["AcceptanceDeadline"], "")
        self.assertEqual(rows[1]["AcceptanceDeadline"], "2028-01-15")

    def test_all_extraction_outputs_exist_before_onelake_upload(self) -> None:
        with patch.object(setup, "TABLES", ["documents"]), \
                patch.object(setup, "content_understanding", return_value=self.cu), \
                patch.object(setup, "DataLakeServiceClient") as factory, \
                patch.object(setup, "fabric") as fabric, \
                patch.object(setup, "check_columns"):
            files = factory.return_value.get_file_system_client.return_value
            upload = files.get_file_client.return_value.upload_data

            def verify_local_outputs(content: bytes, *, overwrite: bool) -> None:
                self.assertTrue(overwrite)
                self.assertEqual(content, (self.outputs / "documents.csv").read_bytes())
                for name, expected in self.results.items():
                    self.assertEqual(
                        json.loads((self.outputs / f"{name}.json").read_text(encoding="utf-8")),
                        expected,
                    )
                fabric.assert_not_called()

            upload.side_effect = verify_local_outputs
            setup.upload_and_load("workspace", "lakehouse")
            upload.assert_called_once()
            files.get_file_client.assert_called_once_with("lakehouse/Files/documents.csv")
            fabric.assert_called_once()

    def test_routing_overrides_misleading_filename_and_heading(self) -> None:
        original = self.data / "pdfs" / "P-200_contract.pdf"
        original.rename(original.with_name("P-200_offer.pdf"))
        content = self.contents.pop("P-200_contract")
        content["markdown"] = "# Offer\nThe parties agree to this binding construction contract."
        self.results["P-200_offer"] = self.results.pop("P-200_contract")
        expected = self.extraction / "expected_document_terms.json"
        values = json.loads(expected.read_text(encoding="utf-8"))
        values["P-200_offer"] = values.pop("P-200_contract")
        expected.write_text(json.dumps(values), encoding="utf-8")

        rows = list(csv.DictReader(io.StringIO(setup.documents_csv(self.cu).decode("utf-8"))))

        self.assertEqual(rows[1]["DocId"], "P-200_offer")
        self.assertEqual(rows[1]["ProjectId"], "P-200")
        self.assertEqual(rows[1]["Title"], "Offer")
        self.assertEqual(rows[1]["DocType"], "Contract")
        self.assertEqual(rows[1]["AcceptanceDeadline"], "2028-01-15")
        self.assertEqual(
            [call.args[0] for call in self.cu.ensure_analyzer.call_args_list],
            [setup.DOCUMENT_ANALYZER, setup.DOCUMENT_ROUTER],
        )
        self.assertTrue(all(
            call.args[0] == setup.DOCUMENT_ROUTER for call in self.cu.analyze_file.call_args_list
        ))

    def test_invalid_routing_is_saved_and_blocks_upload_without_filename_fallback(self) -> None:
        for contents in (
            [],
            [self.contents["P-100_offer"], self.contents["P-200_contract"]],
            [dict(self.contents["P-100_offer"], category="Other")],
            [dict(self.contents["P-100_offer"], analyzerId=setup.DOCUMENT_ROUTER)],
            [{"category": "Offer", "analyzerId": setup.DOCUMENT_ANALYZER}],
        ):
            with self.subTest(contents=contents), \
                    patch.object(setup, "TABLES", ["documents"]), \
                    patch.object(setup, "content_understanding", return_value=self.cu), \
                    patch.object(setup, "DataLakeServiceClient") as factory, \
                    patch.object(setup, "fabric") as fabric:
                self.results["P-100_offer"]["contents"] = contents
                with self.assertRaisesRegex(ValueError, "P-100_offer.pdf:"):
                    setup.upload_and_load("workspace", "lakehouse")
                saved = json.loads((self.outputs / "P-100_offer.json").read_text(encoding="utf-8"))
                self.assertEqual(saved, self.results["P-100_offer"])
                factory.return_value.get_file_system_client.return_value.get_file_client.assert_not_called()
                fabric.assert_not_called()

    def test_cu_client_retains_all_routed_content_and_metadata(self) -> None:
        result = {
            "analyzerId": setup.DOCUMENT_ROUTER,
            "contents": [self.contents["P-100_offer"], self.contents["P-200_contract"]],
            "warnings": [],
        }
        client = setup.ContentUnderstanding("https://cu.example", Mock())
        with patch.object(client, "_headers", return_value={}), \
                patch.object(client, "_wait", return_value={"result": result}), \
                patch("content_understanding.requests.post") as post:
            post.return_value.ok = True
            post.return_value.text = ""
            actual = client.analyze_file(
                setup.DOCUMENT_ROUTER, self.data / "pdfs" / "P-100_offer.pdf",
            )
        self.assertEqual(actual, result)
        self.assertIn("projectDocumentRouter:analyzeBinary", post.call_args.args[0])

    def test_local_csv_write_failure_prevents_all_onelake_uploads(self) -> None:
        self.outputs.mkdir()
        (self.outputs / "documents.csv").mkdir()
        with patch.object(setup, "TABLES", ["documents"]), \
                patch.object(setup, "content_understanding", return_value=self.cu), \
                patch.object(setup, "DataLakeServiceClient") as factory, \
                patch.object(setup, "fabric") as fabric:
            with self.assertRaises(IsADirectoryError):
                setup.upload_and_load("workspace", "lakehouse")
            factory.return_value.get_file_system_client.return_value.get_file_client.assert_not_called()
            fabric.assert_not_called()


if __name__ == "__main__":
    unittest.main()
