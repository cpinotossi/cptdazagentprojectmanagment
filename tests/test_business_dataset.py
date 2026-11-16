import csv
import hashlib
import json
import re
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class BusinessDatasetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.data = json.loads((ROOT / "evaluations/business_tests.json").read_text())
        self.cases = self.data["test_cases"]

    def test_six_cases_match_foundry_export_and_have_reviewable_rubrics(self) -> None:
        self.assertEqual([case["id"] for case in self.cases], [f"B-{i:03}" for i in range(1, 7)])
        self.assertEqual(
            [case["category"] for case in self.cases],
            ["email", "email", "question", "question", "news_article", "question"],
        )
        exported = [
            json.loads(line)
            for line in (ROOT / "evaluations/generated/business_reference.jsonl").read_text().splitlines()
        ]
        for row in exported:
            for field in ("criteria", "critical_errors"):
                row[field] = json.loads(row[field])
        self.assertEqual(exported, self.cases)
        for case in self.cases:
            with self.subTest(case=case["id"]):
                for field in ("query", "context", "ground_truth", "criteria", "critical_errors", "sources"):
                    self.assertTrue(case[field])
                self.assertNotIn("response", case)
                for group in ("criteria", "critical_errors"):
                    self.assertEqual(len({item["id"] for item in case[group]}), len(case[group]))
                    self.assertTrue(all(item["description"].strip() for item in case[group]))

    def test_source_hashes_and_non_pdf_context_quotes_match_current_files(self) -> None:
        sources = {source["path"]: source for source in self.data["source_documents"]}
        for path, source in sources.items():
            with self.subTest(path=path):
                self.assertEqual(hashlib.sha256((ROOT / path).read_bytes()).hexdigest(), source["sha256"])
        for case in self.cases:
            declared = {source["path"] for source in case["sources"]}
            blocks = re.findall(r"\[([^,\]]+)(?:,[^\]]+)?\]\n(.*?)(?=\n\n\[|\Z)", case["context"], re.S)
            self.assertEqual({path for path, _ in blocks}, declared)
            for path, excerpt in blocks:
                self.assertIn(path, sources)
                if path.endswith(".pdf"):
                    continue
                original = (ROOT / path).read_text()
                for line in excerpt.splitlines():
                    if line.strip():
                        with self.subTest(case=case["id"], path=path, quote=line):
                            self.assertIn(line, original)

    def test_numeric_reference_values_match_inputs_and_document_terms(self) -> None:
        terms = json.loads((ROOT / "extraction/expected_document_terms.json").read_text())
        criteria = {
            case["id"]: {item["id"]: item for item in case["criteria"]}
            for case in self.cases
        }
        budget = terms["P-200_contract"]["BudgetEur"]
        for prefix, forecast in (("a", 5500000), ("b", 5550000)):
            self.assertEqual(criteria["B-003"][f"{prefix}_eur"]["expected_number"], forecast - budget)
            self.assertEqual(
                criteria["B-003"][f"{prefix}_percent"]["expected_number"],
                (forecast - budget) / budget * 100,
            )
        self.assertEqual(
            criteria["B-004"]["estimate"]["expected_number"],
            80 * terms["P-100_offer"]["ChangeRateEurPerHour"],
        )

    def test_supplier_scenario_links_open_deliveries_to_different_project_windows(self) -> None:
        def records(table, key):
            with (ROOT / "data" / f"{table}.csv").open() as source:
                rows = list(csv.DictReader(source))
            self.assertEqual(len(rows), len({row[key] for row in rows}))
            return {row[key]: row for row in rows}

        suppliers = records("suppliers", "SupplierId")
        materials = records("materials", "MaterialId")
        tasks = records("tasks", "TaskId")
        projects = records("projects", "ProjectId")
        managers = records("project_managers", "ManagerId")
        deliveries = records("material_deliveries", "DeliveryId")
        conflicts = {}
        case = next(case for case in self.cases if case["id"] == "B-005")
        for delivery_id, delivery in deliveries.items():
            with self.subTest(delivery=delivery_id):
                task = tasks[delivery["TaskId"]]
                project = projects[task["ProjectId"]]
                manager = managers[project["ManagerId"]]
                self.assertIn(suppliers[delivery["SupplierId"]]["Name"], case["query"])
                self.assertIn("MW-035", materials[delivery["MaterialId"]]["Name"])
                self.assertEqual(delivery["Status"], "Planned")
                self.assertGreater(float(delivery["Quantity"]), 0)
                self.assertIn(manager["Email"], case["context"])
                planned = date.fromisoformat(delivery["PlannedDeliveryDate"])
                self.assertLessEqual(date.fromisoformat(task["StartDate"]), planned)
                self.assertLessEqual(planned, date.fromisoformat(task["EndDate"]))
                conflicts[project["ProjectId"]] = date(2027, 8, 1) <= planned <= date(2027, 9, 30)
        self.assertEqual(conflicts, {"P-100": True, "P-200": False})

    def test_supplier_replacement_preserves_other_five_cases(self) -> None:
        previous = json.loads((ROOT / "evaluations/archive/business_tests_before_supplier_scenario.json").read_text())
        self.assertEqual(
            [case for case in self.cases if case["id"] != "B-005"],
            [case for case in previous["test_cases"] if case["id"] != "B-005"],
        )

    def test_alternative_supplier_is_linked_by_portfolio_not_by_existing_deliveries(self) -> None:
        def rows(table):
            with (ROOT / "data" / f"{table}.csv").open() as source:
                return list(csv.DictReader(source))

        suppliers = {row["SupplierId"]: row for row in rows("suppliers")}
        materials = {row["MaterialId"] for row in rows("materials")}
        portfolio = rows("supplier_materials")
        pairs = {(row["SupplierId"], row["MaterialId"]) for row in portfolio}
        self.assertEqual(len(pairs), len(portfolio))
        for supplier_id, material_id in pairs:
            self.assertIn(supplier_id, suppliers)
            self.assertIn(material_id, materials)
        deliveries = rows("material_deliveries")
        candidates = {
            suppliers[supplier_id]["Name"]
            for supplier_id, material_id in pairs
            if material_id == "MAT-035" and supplier_id != "S-100"
        }
        self.assertEqual(candidates, {"IsolierPartner GmbH"})
        self.assertFalse(any(row["SupplierId"] == "S-200" for row in deliveries))
        self.assertFalse(any(material_id == "UNKNOWN" for _, material_id in pairs))
        for delivery in deliveries:
            self.assertIn((delivery["SupplierId"], delivery["MaterialId"]), pairs)
        case = next(case for case in self.cases if case["id"] == "B-005")
        self.assertIn("S-200,IsolierPartner GmbH", case["context"])
        self.assertIn("S-200,MAT-035", case["context"])


if __name__ == "__main__":
    unittest.main()
