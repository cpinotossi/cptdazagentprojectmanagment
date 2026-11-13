import json
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class DeploymentIdentityTests(unittest.TestCase):
    def test_bootstrap_trust_is_environment_scoped_and_has_only_contributor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "identity.json"
            subprocess.run(
                ["az", "bicep", "build", "--file", str(ROOT / "infra/deployment-identity.bicep"),
                 "--outfile", str(output)],
                check=True, capture_output=True, text=True,
            )
            template = json.loads(output.read_text())
        resources = template["resources"]
        self.assertEqual(len(resources), 3)
        federation = next(r for r in resources if r["type"].endswith("/federatedIdentityCredentials"))
        self.assertEqual(federation["properties"]["issuer"], "https://token.actions.githubusercontent.com")
        self.assertEqual(federation["properties"]["audiences"], ["api://AzureADTokenExchange"])
        self.assertEqual(
            federation["properties"]["subject"],
            "[format('repo:{0}:environment:{1}', parameters('githubRepository'), parameters('githubEnvironment'))]",
        )
        self.assertEqual(
            template["parameters"]["githubRepository"]["defaultValue"],
            "cpinotossi@8876980/cptdazagentprojectmanagment@1442137267",
        )
        self.assertEqual(template["parameters"]["githubEnvironment"]["defaultValue"], "azure-demo")
        role = next(r for r in resources if r["type"] == "Microsoft.Authorization/roleAssignments")
        self.assertEqual(role["properties"]["principalType"], "ServicePrincipal")
        self.assertNotIn("scope", role)
        self.assertIn("b24988ac-6180-42a0-ab88-20f7382dd24c", template["variables"]["contributorRole"])
        self.assertNotIn("credentials", template["outputs"])


if __name__ == "__main__":
    unittest.main()
