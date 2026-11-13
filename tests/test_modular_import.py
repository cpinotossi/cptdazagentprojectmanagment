import csv
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import import_fabric
import setup
from create_demo_pdf import create_pdf
from pypdf import PdfReader
from semantic_refresh import SemanticModel, csv_rows, row_fingerprint


class RefreshTests(unittest.TestCase):
    def setUp(self) -> None:
        self.model = SemanticModel("workspace", "model", Mock())

    def test_refresh_polls_regional_location_through_canonical_url(self) -> None:
        request_id = "22581255-113e-4927-9e05-56f5396325ce"
        accepted = Mock(status_code=202, headers={
            "Location": f"https://regional.analysis.windows.net/v1.0/myorg/datasets/model/refreshes/{request_id}",
        })
        pending = Mock()
        pending.json.return_value = {"status": "Unknown"}
        completed = Mock()
        completed.json.return_value = {"status": "Completed"}
        with patch.object(self.model, "request", side_effect=[accepted, pending, completed]) as request, \
                patch("semantic_refresh.time.sleep"):
            result = self.model.refresh()
        self.assertEqual(result, f"{self.model.base}/refreshes/{request_id}")
        self.assertEqual(request.call_args.args, ("GET", result))

    def test_failed_or_timed_out_refresh_never_reports_success(self) -> None:
        accepted = Mock(status_code=202, headers={"x-ms-request-id": "22581255-113e-4927-9e05-56f5396325ce"})
        failed = Mock()
        failed.json.return_value = {"status": "Failed", "messages": ["source unavailable"]}
        with patch.object(self.model, "request", side_effect=[accepted, failed]):
            with self.assertRaisesRegex(RuntimeError, "source unavailable"):
                self.model.refresh()
        with patch.object(self.model, "request", return_value=accepted):
            with self.assertRaisesRegex(TimeoutError, "Refresh still running"):
                self.model.refresh(timeout=0)

    def test_same_row_count_but_stale_value_requires_reframing(self) -> None:
        expected = b"DocId,Amount,Day,Optional\nD1,15,2027-01-01,\n"
        schema = {"DocId": "string", "Amount": "double", "Day": "dateTime", "Optional": "string"}
        stale = [{"DocId": "D1", "Amount": 14.0, "Day": "2027-01-01T00:00:00", "Optional": None}]
        fresh = [dict(stale[0], Amount=15.0)]
        with patch.object(self.model, "refresh", return_value="refresh-url") as refresh, \
                patch.object(self.model, "rows", side_effect=[stale, fresh]), \
                patch("semantic_refresh.time.sleep"):
            result = self.model.refresh_and_verify({"documents": expected}, {"documents": schema})
        self.assertEqual(refresh.call_count, 2)
        self.assertEqual(result["verified_rows"], {"documents": 1})

    def test_query_errors_and_partial_tables_are_not_snapshots(self) -> None:
        bodies = [
            {"results": [{"error": {"message": "truncated"}}]},
            {"results": [{"tables": [{"rows": [{"documents[DocId]": "D1"}], "error": {"message": "limit"}}]}]},
            {"results": [{"tables": [{"rows": [{"documents[DocId]": "D1"}]}], "error": {"message": "query"}}]},
        ]
        for body in bodies:
            with self.subTest(body=body), patch.object(self.model, "request") as request:
                request.return_value.json.return_value = body
                with self.assertRaises(RuntimeError):
                    self.model.rows("documents")

    def test_verification_rejects_malformed_csv_and_preserves_duplicate_counts(self) -> None:
        for content in (b"A,A\n1,2\n", b"A,B\n1\n", b"A\n1,2\n"):
            with self.assertRaises(ValueError):
                csv_rows(content)
        schema = {"Id": "string"}
        self.assertNotEqual(
            row_fingerprint([{"Id": "D1"}, {"Id": "D1"}], schema),
            row_fingerprint([{"Id": "D1"}], schema),
        )


class ModularImportTests(unittest.TestCase):
    def test_independent_actions_call_only_required_importers(self) -> None:
        for target in ("csv", "ontology", "semantic"):
            with self.subTest(target=target), \
                    patch.object(import_fabric, "existing_item", return_value="id"), \
                    patch.object(setup, "csv_sources", return_value={"emails": b"Id\n1\n"}) as csv_source, \
                    patch.object(setup, "content_understanding") as cu, \
                    patch.object(setup, "upload_tables") as upload, \
                    patch.object(setup, "import_semantic", return_value="model") as semantic, \
                    patch.object(setup, "import_ontology", return_value="ontology") as ontology, \
                    patch.object(setup, "refresh_and_verify", return_value={"verified_rows": {}}) as verify, \
                    patch.object(import_fabric, "SemanticModel") as model:
                model.return_value.rows.return_value = []
                import_fabric.run_import(target, tables=["emails"] if target == "csv" else None)
                cu.assert_not_called()
                if target == "csv":
                    csv_source.assert_called_once_with(["emails"])
                    upload.assert_called_once_with("id", "id", {"emails": b"Id\n1\n"})
                    semantic.assert_not_called()
                    ontology.assert_not_called()
                    verify.assert_called_once()
                elif target == "semantic":
                    semantic.assert_called_once()
                    upload.assert_not_called()
                    ontology.assert_not_called()
                    verify.assert_called_once()
                else:
                    ontology.assert_called_once()
                    upload.assert_not_called()
                    semantic.assert_not_called()
                    verify.assert_not_called()

    def test_single_pdf_update_merges_live_rows_and_preserves_others(self) -> None:
        schema = {"DocId": "string", "Content": "string", "TermStart": "dateTime"}
        current = [
            {"DocId": "P-100_offer", "Content": "unchanged", "TermStart": "2027-01-01T00:00:00"},
            {"DocId": "P-200_offer", "Content": "old", "TermStart": None},
        ]
        updates = [{"DocId": "P-200_offer", "Content": "new", "TermStart": ""}]
        with patch.object(setup, "table_schema", return_value=schema):
            content = import_fabric.merge_document_rows(current, updates, list(schema))
        rows = list(csv.DictReader(io.StringIO(content.decode("utf-8"))))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]["Content"], "new")
        self.assertEqual(rows[0]["Content"], "unchanged")
        self.assertEqual(rows[0]["TermStart"], "2027-01-01")
        with patch.object(setup, "table_schema", return_value=schema):
            with self.assertRaisesRegex(ValueError, "Duplicate"):
                import_fabric.merge_document_rows(current + current, updates, list(schema))

    def test_all_csv_headers_are_validated_before_first_upload(self) -> None:
        with patch.object(setup, "check_columns", side_effect=[None, SystemExit("bad schema")]), \
                patch.object(setup, "DataLakeServiceClient") as factory:
            with self.assertRaisesRegex(SystemExit, "bad schema"):
                setup.upload_tables("workspace", "lakehouse", {
                    "projects": b"ProjectId\nP-100\n", "emails": b"wrong\nvalue\n",
                })
            factory.assert_not_called()

    def test_full_setup_refreshes_uploaded_data_before_ontology(self) -> None:
        with patch.object(setup, "env", return_value="capacity"), \
                patch.object(setup, "fabric") as fabric, \
                patch.object(setup, "find", return_value={"id": "id"}), \
                patch.object(setup, "upload_and_load", return_value={"documents": b"data"}) as upload, \
                patch.object(setup, "import_semantic", return_value="model") as semantic, \
                patch.object(setup, "refresh_and_verify") as verify, \
                patch.object(setup, "import_ontology", return_value="ontology") as ontology:
            fabric.return_value = {"value": [{"displayName": "capacity", "state": "Active", "id": "id"}]}
            calls = Mock()
            for name, child in (("upload", upload), ("semantic", semantic), ("verify", verify), ("ontology", ontology)):
                calls.attach_mock(child, name)
            self.assertEqual(setup.setup_fabric(), ("id", "ontology"))
        self.assertEqual([call[0] for call in calls.mock_calls], ["upload", "semantic", "verify", "ontology"])
        verify.assert_called_once_with("id", "model", {"documents": b"data"})

    def test_sql_sync_failure_inside_successful_http_response_is_an_error(self) -> None:
        with patch.object(setup, "fabric", return_value={
            "value": [{"tableName": "documents", "status": "Failure", "error": {"message": "not found"}}],
        }):
            with self.assertRaisesRegex(RuntimeError, "synchronization failed"):
                setup.refresh_sql_schema("workspace", {"id": "sql"})

    def test_sql_notrun_requires_previous_sync_and_is_reported_explicitly(self) -> None:
        status = {"tableName": "documents", "status": "NotRun"}
        with patch.object(setup, "fabric", return_value={"value": [status]}):
            with self.assertRaises(RuntimeError):
                setup.refresh_sql_schema("workspace", {"id": "sql"})
        status["lastSuccessfulSyncDateTime"] = "2026-11-12T09:00:00Z"
        with patch.object(setup, "fabric", return_value={"value": [status]}), \
                patch("builtins.print") as output:
            setup.refresh_sql_schema("workspace", {"id": "sql"})
        self.assertIn("was not refreshed (NotRun)", output.call_args_list[0].args[0])

    def test_table_load_polls_regional_operation_through_canonical_fabric_api(self) -> None:
        operation = "8c0d7822-cf37-4410-ae56-5f62533d22d6"
        accepted = Mock(headers={
            "Location": f"https://regional.analysis.windows.net/v1/operations/{operation}",
            "Retry-After": "0",
        })
        completed = Mock(ok=True)
        completed.json.return_value = {"status": "Succeeded"}
        with patch.object(setup.requests, "get", return_value=completed) as get, \
                patch.object(setup, "headers", return_value={}), \
                patch.object(setup.time, "sleep"):
            result = setup.wait_operation(accepted)
        self.assertEqual(result, f"{setup.FABRIC}/operations/{operation}")
        self.assertEqual(get.call_args.args[0], result)


class DemoPdfTests(unittest.TestCase):
    def test_demo_text_is_rendered_and_can_be_changed_without_editing_other_pdfs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "document.txt"
            destination = Path(temporary) / "P-200_offer.pdf"
            source.write_text("Offer\nBinding construction contract.\nRevision: 1.", encoding="utf-8")
            create_pdf(source, destination)
            self.assertIn("Revision: 1.", PdfReader(destination).pages[0].extract_text())
            source.write_text("Offer\nBinding construction contract.\nRevision: 2.", encoding="utf-8")
            create_pdf(source, destination)
            self.assertIn("Revision: 2.", PdfReader(destination).pages[0].extract_text())


if __name__ == "__main__":
    unittest.main()
