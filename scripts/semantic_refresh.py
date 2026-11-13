"""Refresh a Direct Lake semantic model and verify its visible table contents."""

import csv
import io
import re
import time
from collections import Counter
from datetime import date
from decimal import Decimal
from urllib.parse import urlparse
from uuid import UUID

import requests
from azure.core.credentials import TokenCredential

POWERBI = "https://api.powerbi.com/v1.0/myorg"


def csv_rows(content: bytes) -> tuple[list[str], list[dict]]:
    reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
    columns = reader.fieldnames
    if not columns or len(columns) != len(set(columns)):
        raise ValueError("CSV must have a nonempty header with unique columns")
    rows = list(reader)
    if any(set(row) != set(columns) or any(value is None for value in row.values()) for row in rows):
        raise ValueError("CSV rows must contain exactly the header columns")
    return columns, rows


def row_fingerprint(rows: list[dict], schema: dict[str, str]) -> Counter:
    def value(raw, kind):
        if raw is None or raw == "":
            return ""
        if kind == "dateTime":
            return date.fromisoformat(str(raw)[:10]).isoformat()
        if kind in {"double", "int64", "decimal"}:
            return Decimal(str(raw))
        return str(raw)

    columns = list(schema)
    for row in rows:
        if set(row) != set(columns):
            raise ValueError("Query or CSV columns differ from the semantic model schema")
    return Counter(tuple(value(row[column], schema[column]) for column in columns) for row in rows)


class SemanticModel:
    def __init__(
        self, workspace_id: str, model_id: str, credential: TokenCredential,
    ) -> None:
        self.base = f"{POWERBI}/groups/{workspace_id}/datasets/{model_id}"
        self.credential = credential

    def request(self, method: str, url: str, **kwargs) -> requests.Response:
        response = requests.request(
            method, url,
            headers={"Authorization": "Bearer " + self.credential.get_token(
                "https://analysis.windows.net/powerbi/api/.default",
            ).token},
            timeout=120, **kwargs,
        )
        if not response.ok:
            raise RuntimeError(f"Semantic model HTTP {response.status_code}: {response.text}")
        return response

    def refresh(self, timeout: float = 900) -> str:
        response = self.request(
            "POST", f"{self.base}/refreshes",
            json={"type": "automatic", "commitMode": "transactional", "retryCount": 0},
        )
        location = response.headers.get("Location", "")
        if response.status_code != 202:
            raise RuntimeError(f"Refresh did not return an accepted operation URL: HTTP {response.status_code}")
        request_id = str(UUID(
            urlparse(location).path.rstrip("/").rsplit("/", 1)[-1]
            if location else response.headers["x-ms-request-id"]
        ))
        # Fabric can return a regional Location; poll through the canonical authenticated API.
        location = f"{self.base}/refreshes/{request_id}"
        print(f"Semantic model refresh: {request_id}", flush=True)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = self.request("GET", location).json()
            status = state.get("status")
            if status == "Completed":
                print("Semantic model refresh completed.", flush=True)
                return location
            if status not in {"Unknown", "NotStarted", "InProgress"}:
                raise RuntimeError(f"Semantic model refresh failed: {state}")
            time.sleep(min(5, max(0, deadline - time.monotonic())))
        raise TimeoutError(f"Refresh still running after {timeout} seconds; operation: {location}")

    def rows(self, table: str) -> list[dict]:
        if not re.fullmatch(r"[a-z_]+", table):
            raise ValueError(f"Invalid table name: {table}")
        body = self.request(
            "POST", f"{self.base}/executeQueries",
            json={"queries": [{"query": f"EVALUATE '{table}'"}],
                  "serializerSettings": {"includeNulls": True}},
        ).json()
        results = body.get("results", [])
        if body.get("error") or len(results) != 1 or results[0].get("error"):
            raise RuntimeError(f"Semantic query failed for {table}: {body}")
        tables = results[0].get("tables", [])
        if len(tables) != 1 or tables[0].get("error") or "rows" not in tables[0]:
            raise RuntimeError(f"Semantic query returned incomplete results for {table}: {body}")
        rows = tables[0]["rows"]
        if len(rows) >= 100_000 or sum(len(row) for row in rows) >= 1_000_000:
            raise ValueError(f"{table}: query limit reached; refusing a potentially truncated snapshot")
        return [
            {name.rsplit("[", 1)[-1].removesuffix("]"): value for name, value in row.items()}
            for row in rows
        ]

    def refresh_and_verify(
        self, sources: dict[str, bytes], schemas: dict[str, dict[str, str]],
        timeout: float = 900,
    ) -> dict:
        expected = {}
        for table, content in sources.items():
            _, rows = csv_rows(content)
            expected[table] = row_fingerprint(rows, schemas[table])
        deadline = time.monotonic() + timeout
        mismatches = list(expected)
        while time.monotonic() < deadline:
            operation = self.refresh(max(1, deadline - time.monotonic()))
            mismatches = []
            counts = {}
            for table, fingerprint in expected.items():
                actual = self.rows(table)
                counts[table] = len(actual)
                if row_fingerprint(actual, schemas[table]) != fingerprint:
                    mismatches.append(table)
            if not mismatches:
                print(f"Verified complete table contents: {counts}", flush=True)
                return {"refresh_url": operation, "verified_rows": counts}
            print(f"Data not yet visible in {mismatches}; waiting before reframing.", flush=True)
            time.sleep(min(10, max(0, deadline - time.monotonic())))
        raise TimeoutError(f"Imported data not visible before timeout; mismatched tables: {mismatches}")
