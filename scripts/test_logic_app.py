"""Manually test the Fabric IQ workflow using existing delegated Azure CLI credentials."""

import argparse
import json
import re
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


def azure_json(*arguments: str) -> dict:
    result = subprocess.run(
        ["az", *arguments, "--output", "json"],
        check=True, capture_output=True, text=True,
    )
    return json.loads(result.stdout)


def validate_callback(url: str, *, original_url: str | None = None) -> None:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or not (parsed.hostname or "").endswith(".logic.azure.com")
        or parsed.port not in (None, 443)
        or not parsed.path.startswith("/workflows/")
        or parsed.username is not None or parsed.password is not None or parsed.fragment
        or {"sig", "sp", "sv", "se"} & parse_qs(parsed.query, keep_blank_values=True).keys()
    ):
        raise ValueError("Expected an HTTPS Consumption trigger URL with SAS disabled.")
    if original_url is None:
        if not parsed.path.endswith("/triggers/When_a_request_is_received/paths/invoke"):
            raise ValueError("Expected the configured Request trigger.")
    else:
        original = urlsplit(original_url)
        prefix = original.path.split("/triggers/", 1)[0] + "/"
        if parsed.hostname != original.hostname or not parsed.path.startswith(prefix):
            raise ValueError("Refusing to forward credentials to another workflow or host.")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Refusing an HTTP redirect while sending an Entra token.")


def validate_assessment(body: dict) -> None:
    if not isinstance(body.get("response"), str) or not body["response"].strip():
        raise ValueError("Workflow returned no assessment text.")
    validation = body["validation"]
    if validation.get("valid") is not True or validation.get("issues") != []:
        raise ValueError("Workflow did not pass independent validation.")
    if not isinstance(body.get("runId"), str) or not body["runId"].strip():
        raise ValueError("Workflow returned no run ID for diagnostics.")
    rules = validation["rules"]
    rule_ids = [rule["ruleId"] for rule in rules]
    if len(rule_ids) != len(set(rule_ids)):
        raise ValueError("Validation contains duplicate rule identities.")
    if any(
        not rule["ruleId"].strip()
        or rule["status"] not in {"applied", "not-applicable"}
        or not rule["reason"].strip()
        for rule in rules
    ):
        raise ValueError("A validated rule is unresolved or has no identity or rationale.")


def invoke_workflow(resource_id: str, query: str) -> dict:
    if not re.fullmatch(
        r"/subscriptions/[0-9a-fA-F-]{36}/resourceGroups/[^/?]+"
        r"/providers/Microsoft\.Logic/workflows/[^/?]+", resource_id,
    ):
        raise ValueError("Expected a Consumption workflow ARM resource ID.")
    resource = azure_json("resource", "show", "--ids", resource_id, "--api-version", "2019-05-01")
    properties = resource["properties"]
    if properties["state"] != "Enabled":
        raise ValueError("Workflow is disabled; explicitly enable it for manual testing.")
    if properties["accessControl"]["triggers"]["sasAuthenticationPolicy"]["state"] != "Disabled":
        raise ValueError("SAS authentication must be disabled before invoking the workflow.")
    callback = azure_json(
        "rest", "--method", "post", "--url",
        f"https://management.azure.com{resource_id}/triggers/"
        "When_a_request_is_received/listCallbackUrl?api-version=2019-05-01",
    )["value"]
    validate_callback(callback)
    return invoke_endpoint(callback, query)


def invoke_endpoint(callback: str, query: str) -> dict:
    """Invoke a preconfigured SAS-free endpoint without requiring ARM read permissions."""
    validate_callback(callback)
    token = azure_json("account", "get-access-token", "--resource", "https://management.azure.com/")["accessToken"]
    fabric_token = azure_json(
        "account", "get-access-token", "--resource", "https://api.fabric.microsoft.com",
    )["accessToken"]
    request = Request(
        callback,
        data=json.dumps({"query": query, "fabricAccessToken": fabric_token}, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    opener = build_opener(NoRedirect())
    deadline = time.monotonic() + 900
    try:
        while True:
            with opener.open(request, timeout=120) as response:
                if response.status != 202:
                    body = json.load(response)
                    break
                poll_url = response.headers["Location"]
            validate_callback(poll_url, original_url=callback)
            if time.monotonic() >= deadline:
                raise TimeoutError("Consumption response did not complete within 15 minutes.")
            time.sleep(5)
            request = Request(poll_url, headers={"Authorization": f"Bearer {token}"})
    except HTTPError as error:
        payload = json.loads(error.read())
        detail = payload.get("error", {})
        raise RuntimeError(
            f"Workflow HTTP {error.code}: {detail.get('code', 'UnspecifiedError')}; "
            f"stage={detail.get('stage', 'unspecified')}; "
            f"runId={payload.get('runId', 'not-started')}"
        ) from error
    validate_assessment(body)
    return body


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resource-id", required=True)
    parser.add_argument("--query", default="Liste alle Projekte.")
    args = parser.parse_args()
    body = invoke_workflow(args.resource_id, args.query)
    print(
        f"Fabric IQ manual test passed: run={body['runId']}, "
        f"validated rules={len(body['validation']['rules'])}."
    )
    print(body["response"])


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, TypeError, RuntimeError, URLError, TimeoutError, subprocess.CalledProcessError) as error:
        print(f"Fabric IQ manual test failed: {error}", file=sys.stderr)
        sys.exit(1)
