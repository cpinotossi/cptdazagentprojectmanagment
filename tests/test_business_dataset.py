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

    @staticmethod
    def core(case: dict, ignored: frozenset = frozenset()) -> dict:
        """The case without evaluation-only annotations (absence flags, anchors) added after the archive."""
        result = {key: value for key, value in case.items() if key not in ignored}
        result["criteria"] = [
            {k: v for k, v in item.items() if k not in ("absence", "anchors")} for item in case["criteria"]
        ]
        return result

    def test_supplier_replacement_preserves_other_five_cases(self) -> None:
        previous = json.loads((ROOT / "evaluations/archive/business_tests_before_supplier_scenario.json").read_text())
        changed = {"B-002", "B-003", "B-005", "B-006"}
        self.assertEqual(
            [self.core(case) for case in self.cases if case["id"] not in changed],
            [self.core(case) for case in previous["test_cases"] if case["id"] not in changed],
        )

    def test_b003_and_b006_differ_from_the_archive_only_by_annotations_and_the_moved_label_note(self) -> None:
        previous = json.loads((ROOT / "evaluations/archive/business_tests_before_supplier_scenario.json").read_text())
        for case_id, ignored in (("B-003", frozenset()), ("B-006", frozenset({"ground_truth"}))):
            before = next(case for case in previous["test_cases"] if case["id"] == case_id)
            after = next(case for case in self.cases if case["id"] == case_id)
            with self.subTest(case=case_id):
                self.assertEqual(self.core(before, ignored), self.core(after, ignored))

    def test_b002_judge_context_adds_only_the_existing_e4_email(self) -> None:
        previous = json.loads((ROOT / "evaluations/archive/business_tests_before_supplier_scenario.json").read_text())
        before = next(case for case in previous["test_cases"] if case["id"] == "B-002")
        after = next(case for case in self.cases if case["id"] == "B-002")
        ignored = frozenset({"context", "sources"})
        self.assertEqual(self.core(before, ignored), self.core(after, ignored))
        e4 = "E-4,P-200,team@isar.example,Holiday schedule,Our office is closed from 20 December to 3 January.,2026-11-02"
        self.assertIn(e4, after["context"])
        self.assertEqual(after["context"].replace(f"[data/emails.csv, MailId=E-4]\n{e4}\n\n", ""), before["context"])

    def test_b005_mandatory_points_are_atomic_criteria(self) -> None:
        case = next(case for case in self.cases if case["id"] == "B-005")
        ids = [item["id"] for item in case["criteria"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue({
            "dl100_delivery", "dl100_project", "dl100_task", "dl100_conflict",
            "clarify_supplier_confirmation", "clarify_replacement_date", "clarify_dependencies", "clarify_buffers",
            "alt_availability", "alt_quantity_needed", "alt_quantity_value", "alt_price", "alt_approval",
            "dl200_no_guarantee", "alt_not_self",
        } <= set(ids))
        retired = {"affected_delivery", "unaffected_delivery", "investigation", "alternative_uncertainty",
                   "dl100_ids", "dl200_ids", "clarify_supplier", "alt_availability_quantity", "alt_price_approval"}
        self.assertFalse(retired & set(ids))
        absence = {item["id"] for item in case["criteria"] if item.get("absence")}
        self.assertEqual(absence, {"dl200_no_guarantee", "resume_not_verified", "alt_not_self", "alt_no_reorder", "policy_scope"})

    def test_anchors_are_well_formed_and_visible_in_the_criterion_text(self) -> None:
        for case in self.cases:
            for item in case["criteria"]:
                for anchor in item.get("anchors", []):
                    alternatives = anchor if isinstance(anchor, list) else [anchor]
                    with self.subTest(case=case["id"], criterion=item["id"]):
                        self.assertTrue(alternatives and all(isinstance(a, str) and a.strip() for a in alternatives))
                        self.assertTrue(any(a in item["description"] for a in alternatives))
                        self.assertFalse(item.get("absence"), "A prohibition cannot require an anchor")

    def test_judge_instructions_are_not_part_of_ground_truth(self) -> None:
        for case in self.cases:
            self.assertNotIn("Quellenlabel", case["ground_truth"])
            self.assertNotIn("fabric-iq-ontology", case["ground_truth"])

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
