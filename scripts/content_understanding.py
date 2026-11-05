"""Small client for Azure Content Understanding.

The fields to extract are not defined here but in the analyzer files in extraction/*.json.
"""
import sys
import time

import requests

API_VERSION = "2025-11-01"
CONTENT_TYPES = {".pdf": "application/pdf", ".eml": "message/rfc822"}


class ContentUnderstanding:
    def __init__(self, endpoint, credential):
        self.endpoint = endpoint.rstrip("/")
        self.credential = credential

    def _headers(self, content_type="application/json"):
        token = self.credential.get_token("https://cognitiveservices.azure.com/.default").token
        return {"Authorization": f"Bearer {token}", "Content-Type": content_type}

    def _url(self, path, **params):
        query = "&".join([f"api-version={API_VERSION}"] + [f"{k}={v}" for k, v in params.items()])
        return f"{self.endpoint}/contentunderstanding/{path}?{query}"

    def _wait(self, response):
        url = response.headers["Operation-Location"]
        for _ in range(150):
            time.sleep(2)
            body = requests.get(url, headers=self._headers()).json()
            if body.get("status") == "Succeeded":
                return body
            if body.get("status") == "Failed":
                sys.exit(f"Content Understanding operation failed: {body}")
        sys.exit("Content Understanding operation timed out")

    def set_defaults(self, model_deployments):
        """Maps model names to deployment names. A new deployment can take a minute to become known."""
        for _ in range(8):
            r = requests.patch(self._url("defaults"), headers=self._headers(), json={"modelDeployments": model_deployments})
            if r.ok:
                return
            if "DeploymentIdNotFound" not in r.text:
                sys.exit(f"Setting Content Understanding defaults failed: {r.status_code} {r.text}")
            time.sleep(20)
        sys.exit(f"Setting Content Understanding defaults failed: {r.text}")

    def ensure_analyzer(self, analyzer_id, definition_path):
        """Creates the analyzer or replaces it, so that the file in extraction/ is the single definition."""
        r = requests.put(self._url(f"analyzers/{analyzer_id}", allowReplace="true"), headers=self._headers(),
                         data=definition_path.read_bytes())
        if not r.ok:
            sys.exit(f"Creating analyzer {analyzer_id} failed: {r.status_code} {r.text}")
        self._wait(r)

    def analyze_file(self, analyzer_id, path):
        """Analyzes a local file (PDF or EML) and returns the first content item with fields and markdown."""
        for _ in range(8):
            r = requests.post(self._url(f"analyzers/{analyzer_id}:analyzeBinary"),
                              headers=self._headers(CONTENT_TYPES[path.suffix.lower()]), data=path.read_bytes())
            if "DeploymentIdNotFound" not in r.text:
                break
            time.sleep(20)  # a deployment that was just updated is briefly unknown
        if not r.ok:
            sys.exit(f"Analyzing {path.name} failed: {r.status_code} {r.text}")
        return self._wait(r)["result"]["contents"][0]


def field_value(field):
    """Returns the plain value of an extracted field, or None when the document does not state it."""
    if not field:
        return None
    for key in ("valueString", "valueNumber", "valueDate", "valueBoolean", "valueInteger"):
        if key in field:
            return field[key]
    return None
