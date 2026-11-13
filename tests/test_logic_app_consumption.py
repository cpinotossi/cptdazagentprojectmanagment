import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ConsumptionTemplateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.templates = {}
        with tempfile.TemporaryDirectory() as directory:
            for name in ("main", "modules/logic-apps-consumption"):
                output = Path(directory) / f"{Path(name).name}.json"
                subprocess.run(
                    ["az", "bicep", "build", "--file", str(ROOT / f"infra/{name}.bicep"),
                     "--outfile", str(output)],
                    check=True, capture_output=True, text=True,
                )
                cls.templates[name] = json.loads(output.read_text())

    def test_consumption_has_no_standard_hosting_and_stays_disabled(self) -> None:
        template = self.templates["modules/logic-apps-consumption"]
        resources = template["resources"]
        self.assertEqual({r["type"] for r in resources}, {
            "Microsoft.Logic/workflows", "Microsoft.Insights/diagnosticSettings",
        })
        workflow = next(r for r in resources if r["type"] == "Microsoft.Logic/workflows")
        self.assertEqual(workflow["properties"]["state"], "Disabled")
        self.assertEqual(workflow["identity"]["type"], "SystemAssigned")
        definition = json.loads(
            (ROOT / "logicapps/ontology-orchestration/workflow.json").read_text(),
        )["definition"]
        reference = re.fullmatch(
            r"\[variables\('([^']+)'\)\.definition\]", workflow["properties"]["definition"],
        )
        self.assertIsNotNone(reference)
        assert reference is not None
        self.assertEqual(template["variables"][reference.group(1)]["definition"], definition)
        actions = definition["actions"]
        self.assertEqual(actions["Reject_unconfigured_integration"]["inputs"]["statusCode"], 503)
        failure = actions["Fail_unconfigured_run"]
        self.assertEqual(failure["inputs"]["runStatus"], "Failed")
        self.assertEqual(failure["runAfter"], {
            "Reject_unconfigured_integration": ["Succeeded"],
        })

    def test_main_embeds_the_same_module_and_reuses_existing_logs(self) -> None:
        main = self.templates["main"]
        module = next(r for r in main["resources"]
                      if r["type"] == "Microsoft.Resources/deployments"
                      and r["name"] == "logic-apps-consumption")
        self.assertTrue(main["parameters"]["deployLogicApp"]["defaultValue"])
        self.assertEqual(module["condition"], "[parameters('deployLogicApp')]")
        self.assertEqual(module["properties"]["template"],
                         self.templates["modules/logic-apps-consumption"])
        workspace = module["properties"]["parameters"]["logAnalyticsWorkspaceResourceId"]["value"]
        self.assertIn("Microsoft.OperationalInsights/workspaces", workspace)
        self.assertIn("logicAppResourceId", main["outputs"])


class ConsumptionDeploymentTests(unittest.TestCase):
    def run_script(
        self, mode: str, *, confirm: bool = False,
    ) -> tuple[subprocess.CompletedProcess[str], list[str]]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            calls = root / "calls"
            az = root / "az"
            az.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$AZ_CALLS"\n')
            az.chmod(0o755)
            environment = os.environ | {
                "PATH": f"{root}:{os.environ['PATH']}",
                "AZ_CALLS": str(calls),
                "AZURE_SUBSCRIPTION_ID": "explicit-subscription",
                "LOGIC_APP_RESOURCE_GROUP": "existing-group",
                "LOGIC_APP_LOCATION": "swedencentral",
            }
            environment.pop("CONFIRM_LOGIC_APP_DEPLOYMENT", None)
            if confirm:
                environment["CONFIRM_LOGIC_APP_DEPLOYMENT"] = "yes"
            result = subprocess.run(
                ["bash", "scripts/deploy_logic_app.sh", mode], cwd=ROOT,
                env=environment, capture_output=True, text=True,
            )
            return result, calls.read_text().splitlines() if calls.exists() else []

    def test_check_is_local_and_old_zip_mode_is_rejected(self) -> None:
        result, calls = self.run_script("check")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0].startswith("bicep build "))
        result, calls = self.run_script("deploy-workflows")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, [])

    def test_what_if_targets_only_the_module_in_incremental_mode(self) -> None:
        result, calls = self.run_script("what-if")
        self.assertEqual(result.returncode, 0, result.stderr)
        preview = next(c for c in calls if c.startswith("deployment group what-if "))
        self.assertIn("--subscription explicit-subscription", preview)
        self.assertIn("--resource-group existing-group", preview)
        self.assertIn("--mode Incremental", preview)
        self.assertIn("--template-file infra/modules/logic-apps-consumption.bicep", preview)
        self.assertNotIn("main.bicep", preview)
        self.assertFalse(any(" create " in c for c in calls))

    def test_deploy_requires_confirmation_and_never_uses_app_service(self) -> None:
        result, calls = self.run_script("deploy")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("CONFIRM_LOGIC_APP_DEPLOYMENT", result.stderr)
        self.assertFalse(any("deployment group create" in c for c in calls))
        result, calls = self.run_script("deploy", confirm=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(any(c.startswith("deployment group create ") for c in calls))
        self.assertFalse(any("webapp" in c or "listKeys" in c for c in calls))


if __name__ == "__main__":
    unittest.main()
