import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "evaluations/evaluators"))

import business_anchors as ba


def item(response, criteria, nested=True):
    row = {"criteria": json.dumps(criteria)}
    row["sample"] = {"output_text": response} if nested else {}
    if not nested:
        row["response"] = response
    return row


class AnchorEvaluatorTests(unittest.TestCase):
    CRITERIA = [
        {"id": "a", "anchors": ["P-100", ["1200", "1.200"]]},
        {"id": "b", "anchors": ["anna.becker@example.com"]},
        {"id": "free", "description": "no anchor"},
    ]

    def test_score_is_the_share_of_found_anchors_and_reads_the_agent_output(self) -> None:
        full = "**P-100** braucht 1.200 m2. Kontakt: Anna.Becker@example.com"
        self.assertEqual(ba.grade({}, item(full, self.CRITERIA)), 1.0)
        self.assertAlmostEqual(ba.grade({}, item("Nur P-100 und 1200", self.CRITERIA)), 2 / 3)

    def test_missing_answer_scores_zero_and_dataset_style_response_is_supported(self) -> None:
        self.assertEqual(ba.grade({}, item("", self.CRITERIA)), 0.0)
        self.assertEqual(ba.grade({}, item("P-100 1200 anna.becker@example.com", self.CRITERIA, nested=False)), 1.0)

    def test_substring_of_a_longer_number_is_a_known_false_match(self) -> None:
        found, total, _ = ba.check_anchors("Budget 5.500.000 EUR", [{"id": "n", "anchors": ["500.000"]}])
        self.assertEqual((found, total), (1, 1))

    def test_forbidden_patterns_lower_the_score_and_do_not_hit_negations(self) -> None:
        criteria = [{"id": "g", "forbidden": ["planm(ä|ae)(ß|ss)ig"], "anchors": ["P-100"]}]
        self.assertEqual(ba.grade({}, item("P-100 ist betroffen", criteria)), 1.0)
        self.assertEqual(ba.grade({}, item("P-100 erfolgt planmäßig", criteria)), 0.5)
        self.assertEqual(ba.forbidden_hits("Es erfolgt PLANMAESSIG", criteria), [("g", "planm(ä|ae)(ß|ss)ig")])

    def test_missing_anchors_are_reported_per_criterion(self) -> None:
        _, _, missing = ba.check_anchors("P-100", self.CRITERIA)
        self.assertEqual([criterion for criterion, _ in missing], ["a", "b"])

    def test_every_real_reference_case_has_anchors_so_the_score_is_meaningful(self) -> None:
        cases = json.loads((ROOT / "evaluations/business_tests.json").read_text())["test_cases"]
        for case in cases:
            with self.subTest(case=case["id"]):
                self.assertGreater(sum(len(c.get("anchors", [])) for c in case["criteria"]), 0)


if __name__ == "__main__":
    unittest.main()
