"""Local judge calibration: replay fixed answers through the evaluator prompt with a direct model call.

Nothing is written to Foundry: no dataset, evaluation or run. The expected labels are never sent to the judge.
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "evaluations/evaluators"))

import business_anchors  # noqa: E402  (the same file Foundry runs as the code-based evaluator)

CALIBRATION_PATH = ROOT / "evaluations/judge_calibration.json"
LEGACY_CONTROLS = (ROOT / "evaluations/business_judge_controls.json",)
REFERENCE_PATH = ROOT / "evaluations/generated/business_reference.jsonl"
EVALUATOR_PATH = ROOT / "evaluations/evaluators/business_correctness.json"
OUTPUT_DIR = ROOT / "evaluations/runs/judge_calibration"
PLACEHOLDER_FIELDS = ("query", "context", "ground_truth", "criteria", "critical_errors", "response")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_reference(path: Path = REFERENCE_PATH) -> dict[str, dict]:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return {row["id"]: row for row in rows}


def apply_edits(text: str, edits: list[list[str]], label: str) -> str:
    for old, new in edits:
        if text.count(old) != 1:
            raise ValueError(f"{label}: edit target must occur exactly once: {old[:60]!r}")
        text = text.replace(old, new)
    return text


def load_items(calibration: dict, legacy: list[Path] = (), reference: dict[str, dict] | None = None) -> list[dict]:
    items, responses, cases = [], {}, {}
    for answer in calibration["answers"]:
        responses[answer["id"]] = answer["response"]
        cases[answer["id"]] = answer["case_id"]
        items.append({**answer, "kind": "real"})
    for variant in calibration["variants"]:
        if variant["base"] not in responses:
            raise ValueError(f"{variant['id']}: unknown or later-defined base {variant['base']}")
        response = apply_edits(responses[variant["base"]], variant["replace"], variant["id"])
        responses[variant["id"]] = response
        cases[variant["id"]] = cases[variant["base"]]
        items.append({
            "id": variant["id"], "case_id": cases[variant["base"]],
            "kind": "variant", "base": variant["base"], "expected_pass": variant["expected_pass"],
            "expected_failed": variant["expected_failed"], "borderline": False, "rationale": variant["rationale"],
            "response": response,
        })
    for path in legacy:
        data = json.loads(path.read_text())
        for control in data["controls"]:
            items.append({
                "id": control["id"], "case_id": data["case_id"], "kind": "legacy",
                "expected_pass": control["expected_pass"], "expected_failed": [], "borderline": False,
                "rationale": f"Legacy control from {path.name}", "response": control["response"],
            })
    ids = [item["id"] for item in items]
    if len(ids) != len(set(ids)):
        raise ValueError("Calibration item IDs must be unique")
    if reference is not None:
        for item in items:
            row = reference.get(item["case_id"])
            if row is None:
                raise ValueError(f"{item['id']}: unknown case {item['case_id']}")
            known = {c["id"] for c in json.loads(row["criteria"])} | {c["id"] for c in json.loads(row["critical_errors"])}
            unknown = set(item["expected_failed"]) - known
            if unknown:
                raise ValueError(f"{item['id']}: expected_failed IDs not in reference: {sorted(unknown)}")
    return items


def render_prompt(prompt_text: str, row: dict, response: str) -> str:
    values = {**{field: row[field] for field in PLACEHOLDER_FIELDS if field != "response"}, "response": response}
    rendered = prompt_text
    for field in PLACEHOLDER_FIELDS:
        rendered = rendered.replace("{{" + field + "}}", values[field])
    leftover = re.findall(r"\{\{\w+\}\}", rendered)
    if leftover:
        raise ValueError(f"Unresolved placeholders: {sorted(set(leftover))}")
    return rendered


def parse_verdict(text: str) -> dict:
    try:
        data = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return {"status": "invalid", "error": "not JSON"}
    if not isinstance(data, dict) or not isinstance(data.get("result"), bool) or not isinstance(data.get("reason"), str):
        return {"status": "invalid", "error": "missing boolean result or string reason"}
    return {"status": "ok", "result": data["result"], "reason": data["reason"]}


LINE = re.compile(r"^\s*([CXU])\s*\|(.*)$")
QUOTE_EDGES = "\"'„“”‚‘’«»"


normalize = business_anchors.normalize


def quote_is_grounded(quote: str, response: str) -> bool:
    """Every ellipsis-separated segment must occur verbatim in the response, in order."""
    cleaned = normalize(quote.strip().strip(QUOTE_EDGES))
    segments = [segment.strip().strip(QUOTE_EDGES).strip() for segment in re.split(r"\.\.\.|…", cleaned)]
    segments = [segment for segment in segments if segment]
    if not segments:
        return False
    haystack, position = normalize(response), 0
    for segment in segments:
        found = haystack.find(segment, position)
        if found < 0:
            return False
        position = found + len(segment)
    return True


def quote_has_anchors(quote: str, anchors: list) -> bool:
    haystack = business_anchors.normalize(quote)
    return all(business_anchors.anchor_found(haystack, anchor) for anchor in anchors)


def parse_reason(reason: str) -> dict:
    items: dict[tuple[str, str], list[dict]] = {}
    unsupported, malformed, last = [], [], None
    for line in reason.splitlines():
        if not line.strip():
            continue
        match = LINE.match(line)
        if not match:
            evidence = last["evidence"].strip() if last else ""
            if last and evidence[:1] in QUOTE_EDGES and not (len(evidence) > 1 and evidence[-1] in QUOTE_EDGES):
                last["evidence"] += " " + line.strip()  # a quote that continues on the next line
            else:
                malformed.append(line[:80])
            continue
        kind, rest = match.groups()
        if kind == "U":
            parts = [part.strip() for part in rest.split("|", 1)]
            unsupported.append({"reason": parts[0], "quote": parts[1] if len(parts) > 1 else ""})
            last = None
            continue
        parts = [part.strip() for part in rest.split("|", 2)]
        if len(parts) != 3:
            malformed.append(line[:80])
            last = None
            continue
        last = {"decision": parts[1].upper(), "evidence": parts[2]}
        items.setdefault((kind, parts[0]), []).append(last)
    return {"items": items, "unsupported": unsupported, "malformed": malformed}


def evaluate_structured(row: dict, response: str, judge_result: bool, reason: str) -> dict:
    """Deterministic verdict from the judge's per-item lines; the judge's own boolean is only cross-checked.

    Several lines for one ID are allowed (one quote per fact); all of them must hold.
    """
    criteria, critical = json.loads(row["criteria"]), json.loads(row["critical_errors"])
    parsed = parse_reason(reason)
    expected = [("C", item["id"]) for item in criteria] + [("X", item["id"]) for item in critical]
    missing = [f"{kind}:{name}" for kind, name in expected if (kind, name) not in parsed["items"]]
    extra = [f"{kind}:{name}" for kind, name in parsed["items"] if (kind, name) not in set(expected)]
    failed, ungrounded, bad, raw_ok = [], [], [], True
    for item in criteria:
        entries = parsed["items"].get(("C", item["id"]))
        if entries is None:
            continue
        if any(entry["decision"] not in ("MET", "NOT_MET") for entry in entries):
            bad.append(f"C:{item['id']}")
        elif any(entry["decision"] == "NOT_MET" for entry in entries):
            failed.append(item["id"])
            raw_ok = False
        else:
            if item.get("absence"):
                supported = all(entry["evidence"].strip().upper() == "ABSENT" for entry in entries)
            else:
                supported = (all(quote_is_grounded(entry["evidence"], response) for entry in entries)
                             and quote_has_anchors(" ".join(entry["evidence"] for entry in entries), item.get("anchors", [])))
            if not supported:
                ungrounded.append(item["id"])
                failed.append(item["id"])
    for item in critical:
        entries = parsed["items"].get(("X", item["id"]))
        if entries is None:
            continue
        if any(entry["decision"] not in ("PRESENT", "ABSENT") for entry in entries):
            bad.append(f"X:{item['id']}")
        elif any(entry["decision"] == "PRESENT" for entry in entries):
            failed.append(item["id"])
            raw_ok = False
    usable = not (missing or extra or bad or parsed["malformed"])
    # U lines are informational: they are counted and reported but never change the verdict.
    return {
        "effective": (not failed) if usable else None,
        "failed": sorted(set(failed)), "ungrounded": ungrounded, "missing": missing, "extra": extra,
        "bad": bad, "malformed": parsed["malformed"], "unsupported": len(parsed["unsupported"]),
        "inconsistent_result": bool(usable and judge_result != raw_ok),
    }


def judge_once(client, model: str, prompt: str, temperature: float, retries: int = 6) -> dict:
    for attempt in range(retries):
        try:
            completion = client.chat.completions.create(
                model=model, messages=[{"role": "user", "content": prompt}], temperature=temperature,
                response_format={"type": "json_object"},
            )
            return parse_verdict(completion.choices[0].message.content)
        except Exception as error:
            if attempt == retries - 1:
                return {"status": "error", "error": f"{type(error).__name__}: {str(error)[:200]}"}
            time.sleep(min(4 * 2 ** attempt, 40))


def majority(values: list[bool]) -> bool | None:
    passes = sum(values)
    return None if not values or passes * 2 == len(values) else passes * 2 > len(values)


def summarize(items: list[dict], verdicts: dict[str, list[dict]]) -> dict:
    rows = []
    for item in items:
        runs = verdicts[item["id"]]
        structured = [run["structured"] for run in runs if run.get("structured")]
        usable = [s for s in structured if s["effective"] is not None]
        effective = [s["effective"] for s in usable]
        judge = [run["result"] for run in runs if run["status"] == "ok"]
        failed_counts: dict[str, int] = {}
        for s in usable:
            for name in s["failed"]:
                failed_counts[name] = failed_counts.get(name, 0) + 1
        failed_set = {name for name, count in failed_counts.items() if count * 2 > len(usable)}
        wanted = set(item["expected_failed"]) if item["expected_pass"] or item["expected_failed"] else None
        rows.append({
            "id": item["id"], "case_id": item["case_id"], "kind": item["kind"],
            "borderline": item["borderline"], "expected_pass": item["expected_pass"],
            "passes": sum(effective), "usable": len(usable), "unusable": len(runs) - len(usable),
            "decision": majority(effective), "judge_passes": sum(judge), "judge_valid": len(judge),
            "judge_decision": majority(judge),
            "stable": len(set(effective)) <= 1 and len(usable) == len(runs),
            "ungrounded_runs": sum(bool(s["ungrounded"]) for s in structured),
            "inconsistent_runs": sum(s["inconsistent_result"] for s in structured),
            "missed": sorted(wanted - failed_set) if wanted is not None else [],
            "extra": sorted(failed_set - wanted) if wanted is not None else [],
        })
    scored = [row for row in rows if not row["borderline"]]
    return {
        "items": rows,
        "false_pass": [r["id"] for r in scored if r["decision"] is True and not r["expected_pass"]],
        "false_fail": [r["id"] for r in scored if r["decision"] is False and r["expected_pass"]],
        "undecided": [r["id"] for r in scored if r["decision"] is None],
        "unstable": [r["id"] for r in rows if not r["stable"]],
        "judge_false_pass": [r["id"] for r in scored if r["judge_decision"] is True and not r["expected_pass"]],
        "judge_false_fail": [r["id"] for r in scored if r["judge_decision"] is False and r["expected_pass"]],
        "agreement": sum(r["decision"] == r["expected_pass"] for r in scored),
        "scored": len(scored),
    }


def run_calibration(client, items: list[dict], reference: dict[str, dict], prompt_text: str, model: str,
                    repeats: int, temperature: float, workers: int = 2) -> dict[str, list[dict]]:
    jobs = [(item, render_prompt(prompt_text, reference[item["case_id"]], item["response"]))
            for item in items for _ in range(repeats)]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda job: judge_once(client, model, job[1], temperature), jobs))
    verdicts: dict[str, list[dict]] = {item["id"]: [] for item in items}
    for (item, _), verdict in zip(jobs, results):
        if verdict["status"] == "ok":
            verdict["structured"] = evaluate_structured(
                reference[item["case_id"]], item["response"], verdict["result"], verdict["reason"])
        verdicts[item["id"]].append(verdict)
    return verdicts


def print_report(summary: dict, repeats: int) -> None:
    print(f"{'ITEM':<42}{'KIND':<9}{'EXPECTED':<10}{'EFFECTIVE':<12}{'JUDGE':<12}NOTE")
    for row in summary["items"]:
        note = []
        if row["borderline"]:
            note.append("borderline")
        if row["unusable"]:
            note.append(f"{row['unusable']} unusable")
        if not row["stable"]:
            note.append("unstable")
        if row["ungrounded_runs"]:
            note.append(f"{row['ungrounded_runs']} ungrounded quote runs")
        if row["inconsistent_runs"]:
            note.append(f"{row['inconsistent_runs']} result!=lines")
        if not row["borderline"] and row["decision"] is not None and row["decision"] != row["expected_pass"]:
            note.append("MISMATCH")
        print(f"{row['id']:<42}{row['kind']:<9}{'PASS' if row['expected_pass'] else 'FAIL':<10}"
              f"{str(row['passes']) + '/' + str(row['usable']) + ' PASS':<12}"
              f"{str(row['judge_passes']) + '/' + str(row['judge_valid']) + ' PASS':<12}{', '.join(note)}")
    print("\nPer criterion (majority of runs), only differences from the expected failures:")
    shown = False
    for row in summary["items"]:
        if row["missed"] or row["extra"]:
            shown = True
            print(f"  {row['id']}: missed={row['missed'] or '-'} extra={row['extra'] or '-'}")
    if not shown:
        print("  none")
    print(f"\nAgreement (effective verdict): {summary['agreement']}/{summary['scored']} "
          f"(majority of {repeats} runs; borderline excluded)")
    print(f"False PASS: {summary['false_pass'] or 'none'}   (judge boolean alone: {summary['judge_false_pass'] or 'none'})")
    print(f"False FAIL: {summary['false_fail'] or 'none'}   (judge boolean alone: {summary['judge_false_fail'] or 'none'})")
    print(f"Undecided/unusable: {summary['undecided'] or 'none'}; unstable: {summary['unstable'] or 'none'}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default=os.environ.get("FOUNDRY_PROJECT_ENDPOINT"))
    parser.add_argument("--judge-model", default="gpt-4.1")
    parser.add_argument("--evaluator", type=Path, default=EVALUATOR_PATH)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--only", nargs="*", help="Restrict to calibration item IDs")
    parser.add_argument("--no-legacy", action="store_true", help="Skip the older synthetic control files")
    parser.add_argument("--strict", action="store_true", help="Exit 1 on any mismatch or invalid verdict")
    args = parser.parse_args()
    if not args.endpoint:
        parser.error("--endpoint or FOUNDRY_PROJECT_ENDPOINT is required")
    if args.repeats < 1:
        parser.error("--repeats must be at least 1")

    from azure.ai.projects import AIProjectClient
    from azure.identity import AzureCliCredential

    reference = load_reference()
    calibration_bytes = CALIBRATION_PATH.read_bytes()
    items = load_items(json.loads(calibration_bytes), [] if args.no_legacy else list(LEGACY_CONTROLS), reference)
    if args.only:
        unknown = set(args.only) - {item["id"] for item in items}
        if unknown:
            parser.error(f"Unknown item IDs: {sorted(unknown)}")
        items = [item for item in items if item["id"] in set(args.only)]
    evaluator_bytes = args.evaluator.read_bytes()
    prompt_text = json.loads(evaluator_bytes)["prompt_text"]

    with AIProjectClient(endpoint=args.endpoint, credential=AzureCliCredential()) as project:
        with project.get_openai_client() as client:
            verdicts = run_calibration(client, items, reference, prompt_text, args.judge_model, args.repeats, args.temperature)
    summary = summarize(items, verdicts)
    print_report(summary, args.repeats)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = OUTPUT_DIR / f"{stamp}.json"
    path.write_text(json.dumps({
        "created_at": stamp, "judge_model": args.judge_model, "temperature": args.temperature, "repeats": args.repeats,
        "evaluator_sha256": sha256(evaluator_bytes), "calibration_sha256": sha256(calibration_bytes),
        "reference_sha256": sha256(REFERENCE_PATH.read_bytes()), "summary": summary,
        "verdicts": verdicts, "label_status": json.loads(calibration_bytes)["label_status"],
    }, ensure_ascii=False, indent=2) + "\n")
    print(f"\nSaved: {path.relative_to(ROOT)} (local only; no Foundry dataset or evaluation created)")
    bad = summary["false_pass"] or summary["false_fail"] or summary["undecided"]
    return 1 if args.strict and bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
