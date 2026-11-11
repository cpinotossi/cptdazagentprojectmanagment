import csv
import hashlib
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class GroundednessDatasetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.data = json.loads((ROOT / "evaluations/groundedness_tests.json").read_text())
        self.cases = self.data["test_cases"]

    def test_ten_reference_cases_match_the_foundry_export(self) -> None:
        self.assertEqual(len(self.cases), 10)
        self.assertEqual(len({case["id"] for case in self.cases}), 10)
        exported = [
            json.loads(line)
            for line in (ROOT / "evaluations/groundedness_tests.jsonl").read_text().splitlines()
        ]
        self.assertEqual(exported, [
            {key: case[key] for key in ("id", "query", "context", "ground_truth")}
            for case in self.cases
        ])
        for case in self.cases:
            with self.subTest(case=case["id"]):
                for field in ("query", "context", "ground_truth", "required_facts", "sources"):
                    self.assertTrue(case[field])
                self.assertNotIn("response", case)

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

    def test_all_five_real_emails_use_qwert_and_the_correct_project_manager(self) -> None:
        def rows(path: str, key: str) -> dict:
            with (ROOT / path).open(newline="") as stream:
                return {row[key]: row for row in csv.DictReader(stream)}

        emails = rows("data/emails.csv", "MailId")
        projects = rows("data/projects.csv", "ProjectId")
        managers = rows("data/project_managers.csv", "ManagerId")
        cases = [case for case in self.cases if case["category"] == "email"]
        self.assertEqual(len(cases), 5)
        self.assertEqual({case["email_id"] for case in cases}, set(emails))
        for case in cases:
            with self.subTest(email=case["email_id"]):
                email = emails[case["email_id"]]
                project = projects[email["ProjectId"]]
                manager = managers[project["ManagerId"]]
                self.assertIn("qwert", case["query"])
                for field in ("Sender", "Subject", "Body", "ReceivedAt"):
                    self.assertIn(email[field], case["query"])
                for value in (project["ProjectId"], project["Name"], manager["Name"], manager["Email"]):
                    self.assertIn(value, case["ground_truth"])
                self.assertIn("\tsynonym qwert", case["context"])


if __name__ == "__main__":
    unittest.main()
