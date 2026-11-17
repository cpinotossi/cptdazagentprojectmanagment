import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import judge_calibration as jc


class FixtureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.reference = jc.load_reference()
        self.calibration = json.loads(jc.CALIBRATION_PATH.read_text())

    def test_fixture_loads_against_current_reference_and_edits_apply_once(self) -> None:
        items = jc.load_items(self.calibration, list(jc.LEGACY_CONTROLS), self.reference)
        self.assertEqual(len({item["id"] for item in items}), len(items))
        self.assertEqual({item["case_id"] for item in items if item["kind"] == "real"}, set(self.reference))
        for item in items:
            self.assertTrue(item["response"].strip())

    def test_every_case_has_a_real_answer_per_agent(self) -> None:
        agents = {(a["case_id"], a["source"]["agent"]) for a in self.calibration["answers"]}
        self.assertEqual(len(agents), 12)

    def test_unknown_expected_criterion_is_rejected(self) -> None:
        bad = json.loads(json.dumps(self.calibration))
        bad["answers"][0]["expected_failed"] = ["no_such_criterion"]
        with self.assertRaises(ValueError):
            jc.load_items(bad, [], self.reference)

    def test_edit_must_match_exactly_once(self) -> None:
        with self.assertRaises(ValueError):
            jc.apply_edits("abc abc", [["abc", "x"]], "t")
        with self.assertRaises(ValueError):
            jc.apply_edits("abc", [["zzz", "x"]], "t")


class PromptTests(unittest.TestCase):
    def test_render_fills_all_fields_and_never_exposes_expected_labels(self) -> None:
        reference = jc.load_reference()
        prompt = json.loads(jc.EVALUATOR_PATH.read_text())["prompt_text"]
        rendered = jc.render_prompt(prompt, reference["B-002"], "CANDIDATE-TEXT")
        self.assertIn("CANDIDATE-TEXT", rendered)
        self.assertIn("E-4,P-200", rendered)
        self.assertNotIn("{{", rendered)
        self.assertNotIn("expected_pass", rendered)

    def test_unresolved_placeholder_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            jc.render_prompt("{{query}} {{unknown}}", {k: "x" for k in jc.PLACEHOLDER_FIELDS}, "r")


class VerdictTests(unittest.TestCase):
    def test_parse_requires_boolean_result_and_string_reason(self) -> None:
        self.assertEqual(jc.parse_verdict('{"result": true, "reason": "ok"}')["result"], True)
        for text in ('{"result": "true", "reason": "x"}', '{"result": true}', "not json", None):
            self.assertEqual(jc.parse_verdict(text)["status"], "invalid")

    def test_summary_counts_false_pass_false_fail_and_ignores_borderline(self) -> None:
        def item(i, expected, borderline=False):
            return {"id": i, "case_id": "B-001", "kind": "real", "borderline": borderline,
                    "expected_pass": expected, "expected_failed": [] if expected else ["x"]}

        def run(effective, failed=()):
            structured = {"effective": effective, "failed": list(failed), "ungrounded": [], "inconsistent_result": False}
            return {"status": "ok", "result": bool(effective), "structured": structured}

        items = [item("a", True), item("b", False), item("c", True), item("d", False, True), item("e", True)]
        verdicts = {
            "a": [run(True)] * 3, "b": [run(True), run(True), run(False, ["x"])], "c": [run(False, ["y"])] * 3,
            "d": [run(True)] * 3, "e": [run(True), {"status": "error"}, run(False, ["x"])],
        }
        summary = jc.summarize(items, verdicts)
        self.assertEqual(summary["false_pass"], ["b"])
        self.assertEqual(summary["false_fail"], ["c"])
        self.assertEqual(summary["undecided"], ["e"])
        self.assertEqual(summary["agreement"], 1)
        self.assertEqual(summary["scored"], 4)
        self.assertIn("b", summary["unstable"])
        self.assertIn("e", summary["unstable"])
        rows = {row["id"]: row for row in summary["items"]}
        self.assertEqual(rows["c"]["extra"], ["y"])
        self.assertEqual(rows["b"]["missed"], ["x"])


class StructuredVerdictTests(unittest.TestCase):
    ROW = {
        "criteria": json.dumps([
            {"id": "a", "description": "x"},
            {"id": "b", "description": "y", "absence": True},
        ]),
        "critical_errors": json.dumps([{"id": "bad", "description": "z"}]),
    }
    RESPONSE = "Die Lieferung DL-100 liegt\nim Stopp."

    def evaluate(self, reason, result=True):
        return jc.evaluate_structured(self.ROW, self.RESPONSE, result, reason)

    def good(self):
        return 'C | a | MET | "Die Lieferung DL-100 liegt im Stopp."\nC | b | MET | ABSENT\nX | bad | ABSENT | -'

    def test_complete_grounded_lines_pass(self) -> None:
        verdict = self.evaluate(self.good())
        self.assertIs(verdict["effective"], True)
        self.assertEqual(verdict["failed"], [])

    def test_quote_missing_from_response_is_demoted_even_if_judge_says_met(self) -> None:
        verdict = self.evaluate(self.good().replace("DL-100 liegt im Stopp", "DL-999 liegt im Stopp"))
        self.assertIs(verdict["effective"], False)
        self.assertEqual(verdict["ungrounded"], ["a"])
        self.assertFalse(verdict["inconsistent_result"])

    def test_boolean_that_contradicts_its_own_lines_is_flagged(self) -> None:
        verdict = self.evaluate(self.good().replace("C | b | MET | ABSENT", "C | b | NOT_MET | -"), result=True)
        self.assertTrue(verdict["inconsistent_result"])
        self.assertIs(verdict["effective"], False)

    def test_not_met_and_critical_error_fail(self) -> None:
        self.assertEqual(self.evaluate(self.good().replace("MET | ABSENT", "NOT_MET | -"))["failed"], ["b"])
        self.assertEqual(self.evaluate(self.good().replace("bad | ABSENT | -", 'bad | PRESENT | "Im Stopp."'))["failed"], ["bad"])

    def test_unsupported_claims_are_counted_but_do_not_change_the_verdict(self) -> None:
        verdict = self.evaluate(self.good() + '\nU | invented | "Im Stopp."')
        self.assertIs(verdict["effective"], True)
        self.assertEqual(verdict["unsupported"], 1)
        self.assertFalse(verdict["inconsistent_result"])

    def test_missing_extra_or_malformed_lines_are_unusable_not_pass_or_fail(self) -> None:
        self.assertIsNone(self.evaluate(self.good().replace("C | b | MET | ABSENT\n", ""))["effective"])
        self.assertIsNone(self.evaluate(self.good() + "\nC | unknown | MET | -")["effective"])
        self.assertIsNone(self.evaluate(self.good() + "\nfree text")["effective"])

    def test_several_lines_for_one_id_must_all_hold(self) -> None:
        extra_ok = self.good() + '\nC | a | MET | "im Stopp."'
        self.assertIs(self.evaluate(extra_ok)["effective"], True)
        self.assertEqual(self.evaluate(self.good() + '\nC | a | MET | "nicht im Text"')["failed"], ["a"])
        self.assertEqual(self.evaluate(self.good() + "\nC | a | NOT_MET | -")["failed"], ["a"])

    def test_anchors_may_be_spread_over_several_quotes_of_the_same_criterion(self) -> None:
        row = {
            "criteria": json.dumps([{"id": "p", "description": "x", "anchors": ["P-100", "Hamburg"]}]),
            "critical_errors": "[]",
        }
        response = "- ID: P-100\n- Name: Office Building Hamburg"
        both = jc.evaluate_structured(row, response, True, 'C | p | MET | "ID: P-100"\nC | p | MET | "Name: Office Building Hamburg"')
        only_id = jc.evaluate_structured(row, response, True, 'C | p | MET | "ID: P-100"')
        self.assertIs(both["effective"], True)
        self.assertEqual(only_id["failed"], ["p"])

    def test_ellipsis_segments_must_each_occur_in_order(self) -> None:
        self.assertTrue(jc.quote_is_grounded("Die Lieferung ... im Stopp", self.RESPONSE))
        self.assertFalse(jc.quote_is_grounded("im Stopp ... Die Lieferung", self.RESPONSE))
        self.assertFalse(jc.quote_is_grounded("Die Lieferung ... nicht vorhanden", self.RESPONSE))

    def test_quote_continuing_on_the_next_line_is_joined(self) -> None:
        response = "| P-100 | Hamburg |\n| P-200 | Munich |"
        row = {"criteria": json.dumps([{"id": "a", "description": "x"}]), "critical_errors": "[]"}
        verdict = jc.evaluate_structured(row, response, True, 'C | a | MET | "| P-100 | Hamburg |\n| P-200 | Munich |"')
        self.assertIs(verdict["effective"], True)

    def test_absence_criterion_accepts_only_the_absent_marker(self) -> None:
        verdict = self.evaluate(self.good().replace("C | b | MET | ABSENT", 'C | b | MET | "Im Stopp."'))
        self.assertEqual(verdict["failed"], ["b"])

    def test_claimed_critical_error_needs_a_quote_with_its_anchor(self) -> None:
        row = {
            "criteria": "[]",
            "critical_errors": json.dumps([{"id": "deadline", "description": "x", "violation_anchors": [["Arbeitstag", "Werktag"]]}]),
        }
        response = "Das muss zwingend bestätigt werden. Frist: ein Arbeitstag."
        topical = jc.evaluate_structured(row, response, False, 'X | deadline | PRESENT | "muss zwingend bestätigt werden"')
        real = jc.evaluate_structured(row, response, False, 'X | deadline | PRESENT | "Frist: ein Arbeitstag."')
        self.assertIs(topical["effective"], True)
        self.assertEqual(topical["discarded_errors"], ["deadline"])
        self.assertFalse(topical["inconsistent_result"])
        self.assertIs(real["effective"], False)
        self.assertEqual(real["failed"], ["deadline"])

    def test_prohibition_violation_needs_a_violation_anchor_and_forbidden_patterns_always_fail(self) -> None:
        row = {
            "criteria": json.dumps([
                {"id": "scope", "description": "x", "absence": True, "violation_anchors": [["Arbeitstag"]]},
                {"id": "promise", "description": "x", "absence": True, "forbidden": ["planm(ä|ae)(ß|ss)ig"]},
            ]),
            "critical_errors": "[]",
        }
        response = "Das muss zwingend bestätigt werden. Die Lieferung erfolgt planmäßig."
        lines = 'C | scope | NOT_MET | "muss zwingend bestätigt werden"\nC | promise | MET | ABSENT'
        verdict = jc.evaluate_structured(row, response, False, lines)
        self.assertEqual(verdict["discarded_errors"], ["scope"])
        self.assertEqual(verdict["failed"], ["promise"])
        self.assertIs(verdict["effective"], False)
        self.assertFalse(verdict["inconsistent_result"])

    def test_real_quote_that_misses_required_anchor_is_rejected(self) -> None:
        row = {
            "criteria": json.dumps([{"id": "qty", "description": "x", "anchors": [["1200", "1.200"]]}]),
            "critical_errors": "[]",
        }
        response = "Gedeckte benötigte Menge. Später: 1.200 m2 für DL-100."
        weak = jc.evaluate_structured(row, response, True, 'C | qty | MET | "Gedeckte benötigte Menge"')
        strong = jc.evaluate_structured(row, response, True, 'C | qty | MET | "1.200 m2 für DL-100"')
        self.assertEqual(weak["failed"], ["qty"])
        self.assertIs(strong["effective"], True)

    def test_markdown_emphasis_and_case_do_not_break_quote_matching(self) -> None:
        self.assertTrue(jc.quote_is_grounded("IsolierPartner GmbH (S-200) ist", "**IsolierPartner GmbH (S-200)** ist weitere Quelle"))

    def test_quote_with_table_pipes_is_kept_in_the_evidence_field(self) -> None:
        response = "| ID | Name |"
        verdict = jc.evaluate_structured(
            {"criteria": json.dumps([{"id": "a", "description": "x"}]), "critical_errors": "[]"},
            response, True, 'C | a | MET | "| ID | Name |"')
        self.assertIs(verdict["effective"], True)


if __name__ == "__main__":
    unittest.main()
