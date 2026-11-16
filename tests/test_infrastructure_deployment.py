import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TENANT = "f71980b2-590a-4de9-90d5-6fbc867da951"
SUBSCRIPTION = "98525264-1eb4-493f-983d-16a330caa7f6"
PRINCIPAL = "11111111-1111-1111-1111-111111111111"


class InfrastructureDeploymentTests(unittest.TestCase):
    def test_demo_parameters_keep_f4_and_separate_human_access_from_oidc(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "parameters.json"
            subprocess.run(
                ["az", "bicep", "build-params", "--file", str(ROOT / "infra/demo.bicepparam"),
                 "--outfile", str(output)], check=True, capture_output=True, text=True,
                env=os.environ | {
                    "FOUNDRY_ACCESS_PRINCIPAL_ID": PRINCIPAL,
                    "FABRIC_ADMIN_UPN": "administrator@example.invalid",
                },
            )
            parameters = json.loads(output.read_text())["parameters"]
        self.assertEqual(parameters["fabricSku"]["value"], "F4")
        self.assertEqual(parameters["principalId"]["value"], PRINCIPAL)
        self.assertEqual(parameters["fabricAdminUpn"]["value"], "administrator@example.invalid")
        self.assertTrue(parameters["deployLogicApp"]["value"])

    def test_rbac_extension_reuses_identity_and_constrains_write_and_delete(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "rbac.json"
            subprocess.run(
                ["az", "bicep", "build", "--file", str(ROOT / "infra/infrastructure-rbac.bicep"),
                 "--outfile", str(output)], check=True, capture_output=True, text=True,
            )
            template = json.loads(output.read_text())
        self.assertEqual(len(template["resources"]), 1)
        role = template["resources"][0]
        self.assertEqual(role["type"], "Microsoft.Authorization/roleAssignments")
        self.assertIn("Microsoft.CognitiveServices/accounts", role["scope"])
        self.assertEqual(role["properties"]["conditionVersion"], "2.0")
        self.assertEqual(role["properties"]["condition"], "[variables('delegationCondition')]")
        condition = template["variables"]["delegationCondition"]
        for action, source in (("write", "@Request"), ("delete", "@Resource")):
            self.assertIn(f"Microsoft.Authorization/roleAssignments/{action}", condition)
            self.assertIn(f"{source}[Microsoft.Authorization/roleAssignments:PrincipalId]", condition)
            self.assertIn(f"{source}[Microsoft.Authorization/roleAssignments:RoleDefinitionId]", condition)
            self.assertIn(f"{source}[Microsoft.Authorization/roleAssignments:PrincipalType]", condition)
        self.assertIn("parameters('accessPrincipalId')", condition)
        self.assertIn("53ca6127-db72-4b80-b1b0-d745d6d5456d", template["variables"]["allowedRoles"])
        self.assertIn("eadc314b-1a2d-4efa-be10-5d325db5065e", template["variables"]["allowedRoles"])
        self.assertNotIn("8e3af657-a8ff-443c-a75c-2fe8c4bcb635", template["variables"]["allowedRoles"])

    def run_script(
        self, mode: str, *, confirm: bool = False, subscription: str = SUBSCRIPTION,
    ) -> tuple[subprocess.CompletedProcess[str], list[list[str]]]:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            calls = path / "calls.jsonl"
            az = path / "az"
            az.write_text(
                '#!/usr/bin/env python3\n'
                'import json, os, sys\n'
                'with open(os.environ["AZ_CALLS"], "a") as output:\n'
                '    output.write(json.dumps(sys.argv[1:]) + "\\n")\n'
                'if sys.argv[1:3] == ["account", "show"]:\n'
                '    print(os.environ["AZURE_TENANT_ID"])\n'
            )
            az.chmod(0o755)
            environment = os.environ | {
                "PATH": f"{path}:{os.environ['PATH']}",
                "AZ_CALLS": str(calls),
                "AZURE_SUBSCRIPTION_ID": subscription,
                "AZURE_TENANT_ID": TENANT,
                "AZURE_RESOURCE_GROUP": "rg-projplan-demo",
                "FOUNDRY_ACCESS_PRINCIPAL_ID": PRINCIPAL,
                "FABRIC_ADMIN_UPN": "administrator@example.invalid",
            }
            environment.pop("CONFIRM_INFRASTRUCTURE_DEPLOYMENT", None)
            if confirm:
                environment["CONFIRM_INFRASTRUCTURE_DEPLOYMENT"] = "yes"
            result = subprocess.run(
                ["bash", str(ROOT / "scripts/deploy_infrastructure.sh"), mode],
                cwd=ROOT, env=environment, capture_output=True, text=True,
            )
            recorded = [json.loads(line) for line in calls.read_text().splitlines()] if calls.exists() else []
            return result, recorded

    def test_check_compiles_only_and_rejects_unknown_operations(self) -> None:
        result, calls = self.run_script("check")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([call[:2] for call in calls], [["bicep", "build"], ["bicep", "build-params"]])
        result, calls = self.run_script("bootstrap-identity")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, [])

    def test_what_if_uses_entire_template_and_never_runs_data_or_agent_setup(self) -> None:
        result, calls = self.run_script("what-if")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("infra/main.bicep", calls[0])
        self.assertIn("infra/demo.bicepparam", calls[1])
        preview = next(call for call in calls if call[:3] == ["deployment", "group", "what-if"])
        self.assertEqual(preview[preview.index("--mode") + 1], "Incremental")
        self.assertEqual(preview[preview.index("--resource-group") + 1], "rg-projplan-demo")
        self.assertEqual(preview[preview.index("--subscription") + 1], SUBSCRIPTION)
        self.assertFalse(any(call[:3] == ["deployment", "group", "create"] for call in calls))
        self.assertFalse(any(call[0] == "group" for call in calls))
        self.assertNotIn("administrator@example.invalid", json.dumps(calls))

    def test_deploy_requires_confirmation_and_fixed_target(self) -> None:
        result, calls = self.run_script("deploy")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("CONFIRM_INFRASTRUCTURE_DEPLOYMENT", result.stderr)
        self.assertEqual(calls, [])
        result, calls = self.run_script("deploy", confirm=True, subscription="wrong-subscription")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, [])
        result, calls = self.run_script("deploy", confirm=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(any(call[:3] == ["deployment", "group", "create"] for call in calls))


if __name__ == "__main__":
    unittest.main()
