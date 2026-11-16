import json
import copy
import io
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from test_logic_app import invoke_workflow, validate_assessment, validate_callback


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
        self.assertEqual(template["parameters"]["workflowState"]["defaultValue"], "Disabled")
        self.assertEqual("[parameters('workflowState')]", workflow["properties"]["state"])
        self.assertIn("'UserAssigned'", workflow["identity"])
        definition = json.loads(
            (ROOT / "logicapps/ontology-orchestration/workflow.json").read_text(),
        )["definition"]
        reference = re.fullmatch(
            r"\[variables\('([^']+)'\)\.definition\]", workflow["properties"]["definition"],
        )
        self.assertIsNotNone(reference)
        assert reference is not None
        self.assertEqual(template["variables"][reference.group(1)]["definition"], definition)
        self.assertNotIn("configured", definition["parameters"])
        self.assertNotIn("configured", workflow["properties"]["parameters"])
        self.assertNotIn("Check_configuration", definition["actions"])
        self.assertEqual(definition["actions"]["Orchestrate"]["runAfter"], {
            "Initialize_stage": ["Succeeded"],
        })

    def test_mandatory_stages_cannot_bypass_failure_or_validation(self) -> None:
        definition = json.loads((ROOT / "logicapps/ontology-orchestration/workflow.json").read_text())["definition"]
        actions = definition["actions"]["Orchestrate"]["actions"]
        for action, prerequisite in (
            ("List_ontology_rules", "List_ontology_entities"),
            ("Rewrite_query", "Begin_rewrite"),
            ("Begin_rewrite", "Entity_names"),
            ("Begin_ontology_query", "Check_rewrite"),
            ("Ask_ontology", "Begin_ontology_query"),
            ("Validate_answer", "Begin_validation"),
            ("Begin_validation", "Answer_text_values"),
            ("Check_validation", "Unresolved_validation_rules"),
        ):
            self.assertEqual(actions[action]["runAfter"], {prerequisite: ["Succeeded"]})
        self.assertIn("Return_assessment", actions["Check_validation"]["actions"])
        self.assertEqual(definition["actions"]["Return_stage_failure"]["inputs"]["statusCode"], 502)
        self.assertEqual(definition["actions"]["Fail_stage"]["inputs"]["runStatus"], "Failed")
        for name in ("Rewrite_query", "Validate_answer"):
            authentication = actions[name]["inputs"]["authentication"]
            self.assertEqual(authentication["type"], "ManagedServiceIdentity")
            self.assertNotIn("agent_reference", actions[name]["inputs"]["body"])
            for message in actions[name]["inputs"]["body"]["input"]:
                self.assertEqual(message["type"], "message")
            self.assertNotIn("fabricAccessToken", json.dumps(actions[name]["inputs"]["body"]))
        for name, tool in (
            ("List_ontology_entities", "list_ontology_entities"),
            ("List_ontology_rules", "list_ontology_rules"),
            ("Ask_ontology", "ask_ontology"),
        ):
            action = actions[name]
            self.assertIn("/ontologyEndpoint", action["inputs"]["uri"])
            self.assertIn("fabricAccessToken", action["inputs"]["headers"]["Authorization"])
            self.assertEqual(action["inputs"]["body"]["params"]["name"], tool)
            self.assertEqual(action["runtimeConfiguration"]["secureData"]["properties"], ["inputs", "outputs"])
        self.assertEqual(actions["List_ontology_entities"]["inputs"]["body"]["params"]["arguments"], {})
        self.assertEqual(actions["List_ontology_rules"]["inputs"]["body"]["params"]["arguments"], {})
        request = actions["Build_ontology_request"]["inputs"]
        self.assertIn("triggerBody()['query']", request)
        self.assertIn("body('Parse_rewrite')['rewrittenQuestion']", request)
        self.assertEqual(actions["Ask_ontology"]["inputs"]["body"]["params"]["arguments"],
                         {"request": "@outputs('Build_ontology_request')"})
        self.assertNotIn("ApiConnection", json.dumps(definition))
        self.assertNotIn("sqlServer", definition["parameters"])
        self.assertIn("fabricAccessToken", definition["triggers"]["When_a_request_is_received"]["inputs"]["schema"]["required"])
        self.assertIn("['isError']", actions["Check_ontology_result"]["expression"])
        self.assertIn("['error']", actions["Check_ontology_result"]["expression"])
        self.assertIn("10000", actions["Check_rewrite"]["expression"])

    def test_compact_response_preserves_validation_and_run_correlation(self) -> None:
        definition = json.loads((ROOT / "logicapps/ontology-orchestration/workflow.json").read_text())["definition"]
        actions = definition["actions"]["Orchestrate"]["actions"]
        response = actions["Check_validation"]["actions"]["Return_assessment"]
        self.assertEqual(response["inputs"]["body"], {
            "response": "@join(body('Answer_text_values'), decodeUriComponent('%0A'))",
            "validation": "@body('Parse_validation')",
            "runId": "@workflow().run.name",
        })
        self.assertIn("body('Parse_validation')['valid']", actions["Check_validation"]["expression"])
        self.assertIn("intersection(body('Validated_rule_ids'), body('Rule_ids'))",
                      actions["Check_validation"]["expression"])
        self.assertIn("intersection(body('Rewritten_rule_ids'), body('Rule_ids'))",
                      actions["Check_rewrite"]["expression"])
        self.assertIn("body('Parse_rewrite')['matchedEntities']", actions["Check_rewrite"]["expression"])
        self.assertEqual(response["runtimeConfiguration"]["secureData"]["properties"], ["inputs"])

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

    def test_secure_data_uses_supported_settings_and_contains_delegated_token(self) -> None:
        definition = json.loads((ROOT / "logicapps/ontology-orchestration/workflow.json").read_text())["definition"]
        def check_actions(actions: dict) -> None:
            for action in actions.values():
                secured = action.get("runtimeConfiguration", {}).get("secureData", {}).get("properties", [])
                if action["type"] in {"Compose", "ParseJson", "Response"}:
                    self.assertNotIn("outputs", secured)
                if action["type"] == "Http" and "fabricAccessToken" in json.dumps(action["inputs"]):
                    self.assertEqual(secured, ["inputs", "outputs"])
                check_actions(action.get("actions", {}))
                check_actions(action.get("else", {}).get("actions", {}))
        check_actions(definition["actions"])
        self.assertEqual(
            definition["triggers"]["When_a_request_is_received"]["runtimeConfiguration"]["secureData"]["properties"],
            ["inputs", "outputs"],
        )


class ConsumptionDeploymentTests(unittest.TestCase):
    def run_script(
        self, mode: str, *, confirm: bool = False, activate: bool = False, smoke_pass: bool = True,
        preview_state: str | None = None,
    ) -> tuple[subprocess.CompletedProcess[str], list[str]]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            calls = root / "calls"
            az = root / "az"
            az.write_text(
                '#!/bin/sh\nprintf "%s\\n" "$*" >> "$AZ_CALLS"\n'
                'case "$1 $2 $3" in\n'
                ' "account show --subscription") echo f71980b2-590a-4de9-90d5-6fbc867da951;;\n'
                ' "deployment group show") echo /subscriptions/98525264-1eb4-493f-983d-16a330caa7f6/resourceGroups/rg-projplan-demo/providers/Microsoft.Logic/workflows/logic-projplan-test;;\n'
                ' "resource show --ids") echo "$TEST_WORKFLOW_STATE";;\n'
                'esac\n'
            )
            az.chmod(0o755)
            python = root / "python3"
            python.write_text(
                '#!/bin/sh\nprintf "python3 %s\\n" "$*" >> "$AZ_CALLS"\n'
                f"exit {0 if smoke_pass else 1}\n"
            )
            python.chmod(0o755)
            environment = os.environ | {
                "PATH": f"{root}:{os.environ['PATH']}",
                "AZ_CALLS": str(calls),
                "AZURE_SUBSCRIPTION_ID": "98525264-1eb4-493f-983d-16a330caa7f6",
                "LOGIC_APP_RESOURCE_GROUP": "rg-projplan-demo",
                "LOGIC_APP_LOCATION": "swedencentral",
                "TEST_WORKFLOW_STATE": "Enabled" if mode == "activate" else "Disabled",
            }
            environment.pop("CONFIRM_LOGIC_APP_DEPLOYMENT", None)
            environment.pop("CONFIRM_LOGIC_APP_ACTIVATION", None)
            environment.pop("LOGIC_APP_PREVIEW_STATE", None)
            if preview_state is not None:
                environment["LOGIC_APP_PREVIEW_STATE"] = preview_state
            if confirm:
                environment["CONFIRM_LOGIC_APP_DEPLOYMENT"] = "yes"
            if activate:
                environment["CONFIRM_LOGIC_APP_ACTIVATION"] = "yes"
            result = subprocess.run(
                ["bash", "scripts/deploy_logic_app.sh", mode], cwd=ROOT,
                env=environment, capture_output=True, text=True,
            )
            return result, calls.read_text().splitlines() if calls.exists() else []

    def test_check_is_local_and_old_zip_mode_is_rejected(self) -> None:
        result, calls = self.run_script("check")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(calls), 2)
        self.assertTrue(calls[0].startswith("bicep build "))
        self.assertTrue(calls[1].startswith("bicep build-params "))
        result, calls = self.run_script("deploy-workflows")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, [])

    def test_what_if_targets_only_the_module_in_incremental_mode(self) -> None:
        result, calls = self.run_script("what-if")
        self.assertEqual(result.returncode, 0, result.stderr)
        preview = next(c for c in calls if c.startswith("deployment group what-if "))
        self.assertIn("--subscription 98525264-1eb4-493f-983d-16a330caa7f6", preview)
        self.assertIn("--resource-group rg-projplan-demo", preview)
        self.assertIn("--mode Incremental", preview)
        self.assertIn("/template.json", preview)
        self.assertIn("/parameters.json", preview)
        self.assertIn("workflowState=Disabled", preview)
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

    def test_activation_is_explicit_and_does_not_run_oidc_as_delegated_user(self) -> None:
        result, calls = self.run_script("activate", confirm=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("CONFIRM_LOGIC_APP_ACTIVATION", result.stderr)
        self.assertFalse(any(c.startswith("deployment group create ") for c in calls))
        result, calls = self.run_script("activate", confirm=True, activate=True, smoke_pass=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("No Fabric inference was run", result.stdout)
        self.assertTrue(any("workflowState=Enabled" in c for c in calls))
        self.assertFalse(any(c.startswith("python3 ") for c in calls))
        self.assertFalse(any("/disable?api-version=" in c for c in calls))

    def test_activation_preview_matches_enabled_state_without_applying(self) -> None:
        result, calls = self.run_script("what-if", preview_state="Enabled")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(any("workflowState=Enabled" in c for c in calls))
        self.assertFalse(any(c.startswith("deployment group create ") for c in calls))
        result, calls = self.run_script("what-if", preview_state="unexpected")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("LOGIC_APP_PREVIEW_STATE", result.stderr)
        self.assertFalse(any(c.startswith("deployment group what-if ") for c in calls))


class WorkflowContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.body = {
            "response": "A supported answer.",
            "validation": {
                "valid": True, "issues": [],
                "rules": [{"ruleId": "example-rule", "status": "applied", "reason": "Supported by IQ evidence."}],
            },
            "runId": "test-run",
        }
        self.callback = "https://prod-01.swedencentral.logic.azure.com/workflows/example/triggers/When_a_request_is_received/paths/invoke?api-version=2016-10-01"

    def test_invalid_compact_assessments_fail(self) -> None:
        validate_assessment(self.body)
        mutations = [
            lambda b: b.update(response=""),
            lambda b: b.update(runId=""),
            lambda b: b.update(runId=None),
            lambda b: b["validation"].update(valid=False),
            lambda b: b["validation"]["rules"].append(copy.deepcopy(b["validation"]["rules"][0])),
            lambda b: b["validation"]["rules"][0].update(status="unresolved"),
            lambda b: b["validation"]["rules"][0].update(ruleId=""),
            lambda b: b["validation"]["rules"][0].update(reason=""),
            lambda b: b["validation"].update(issues=["Missing evidence"]),
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                body = copy.deepcopy(self.body)
                mutation(body)
                with self.assertRaises(ValueError):
                    validate_assessment(body)

    def test_callback_rejects_sas_and_cross_host_or_workflow_polling(self) -> None:
        validate_callback(self.callback)
        for url in (
            self.callback + "&sig=secret",
            self.callback + "&sig=",
            self.callback.replace("https:", "http:"),
            self.callback.replace("logic.azure.com", "example.invalid"),
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_callback(url)
        poll = self.callback.split("/triggers/")[0] + "/runs/test/operations/result"
        validate_callback(poll, original_url=self.callback)
        with self.assertRaises(ValueError):
            validate_callback(poll.replace("/workflows/example/", "/workflows/another/"), original_url=self.callback)

    def test_async_invocation_separates_arm_and_fabric_tokens_without_leaking_to_poll(self) -> None:
        first = MagicMock()
        first.status = 202
        first.headers = {"Location": self.callback.split("/triggers/")[0] + "/runs/test/operations/result"}
        first.__enter__.return_value = first
        final = io.BytesIO(json.dumps(self.body).encode())
        final.status = 200
        opener = MagicMock()
        opener.open.side_effect = [first, final]
        resource = {"properties": {"state": "Enabled", "accessControl": {"triggers": {"sasAuthenticationPolicy": {"state": "Disabled"}}}}}
        with patch("test_logic_app.azure_json", side_effect=[
            resource, {"value": self.callback}, {"accessToken": "arm-test-token"},
            {"accessToken": "fabric-test-token"},
        ]), \
             patch("test_logic_app.build_opener", return_value=opener), patch("test_logic_app.time.sleep"):
            result = invoke_workflow(
                "/subscriptions/98525264-1eb4-493f-983d-16a330caa7f6/resourceGroups/rg-projplan-demo/providers/Microsoft.Logic/workflows/example",
                "Original query without evaluator context.",
            )
        self.assertEqual(result, self.body)
        posted, polled = (call.args[0] for call in opener.open.call_args_list)
        self.assertEqual(json.loads(posted.data), {
            "query": "Original query without evaluator context.", "fabricAccessToken": "fabric-test-token",
        })
        self.assertIsNone(polled.data)
        self.assertEqual(posted.headers["Authorization"], polled.headers["Authorization"])
        self.assertEqual(posted.headers["Authorization"], "Bearer arm-test-token")
        self.assertNotIn("fabric-test-token", str(polled.headers))

    def test_no_applicable_catalog_rules_is_supported(self) -> None:
        self.body["validation"]["rules"] = []
        validate_assessment(self.body)

if __name__ == "__main__":
    unittest.main()
