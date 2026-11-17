"""Deterministic anchor check for the Foundry code-based evaluator.

Self-contained on purpose: the file text is uploaded as the evaluator and runs in
Foundry's sandbox (no network). A criterion may list "anchors": strings the answer
must contain. An anchor is a string or a list of alternative spellings. The score
is the share of anchors found in the generated answer (0.0 to 1.0).
"""

import json
import unicodedata


def normalize(text):
    text = unicodedata.normalize("NFC", str(text)).replace("*", "").replace("`", "").replace("\u00a0", " ")
    return " ".join(text.split()).casefold()


def anchor_found(haystack, anchor):
    alternatives = anchor if isinstance(anchor, list) else [anchor]
    return any(normalize(alternative) in haystack for alternative in alternatives)


def check_anchors(response, criteria):
    """Return (found, total, missing) where missing lists (criterion id, anchor)."""
    haystack = normalize(response)
    found, total, missing = 0, 0, []
    for criterion in criteria:
        for anchor in criterion.get("anchors", []):
            total += 1
            if anchor_found(haystack, anchor):
                found += 1
            else:
                missing.append((criterion["id"], anchor))
    return found, total, missing


def generated_response(sample, item):
    if isinstance(item, dict):
        nested = item.get("sample")
        if isinstance(nested, dict) and nested.get("output_text"):
            return nested["output_text"]
        if item.get("response"):
            return item["response"]
    if isinstance(sample, dict) and sample.get("output_text"):
        return sample["output_text"]
    return ""


def grade(sample, item):
    response = generated_response(sample, item)
    if not response:
        return 0.0
    criteria = item.get("criteria", "[]") if isinstance(item, dict) else "[]"
    if isinstance(criteria, str):
        criteria = json.loads(criteria)
    found, total, _ = check_anchors(response, criteria)
    return found / total if total else 1.0
