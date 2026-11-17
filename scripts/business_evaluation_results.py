"""Validate and summarize Foundry judgments without locally grading answers."""


def judgments(item: dict, row_id: str) -> tuple[dict, dict | None]:
    """Return the correctness judgment and the optional anchor score of one output item."""
    if item.get("status") != "completed" or not item.get("results"):
        raise ValueError(f"{row_id}: incomplete evaluation")
    results = item["results"]
    named = {result.get("name"): result for result in results}
    correctness = named.get("Business correctness") or (
        results[0] if len(results) == 1 and results[0].get("name") is None else None
    )
    if correctness is None:
        raise ValueError(f"{row_id}: incomplete evaluation")
    if correctness.get("status") != "completed" or type(correctness.get("passed")) is not bool:
        raise ValueError(f"{row_id}: missing or errored Foundry decision")
    anchors = named.get("Business anchors")
    if anchors is not None and (
        anchors.get("status") != "completed" or type(anchors.get("score")) not in (int, float)
    ):
        raise ValueError(f"{row_id}: missing or errored anchor score")
    return correctness, anchors


def comparison_summary(record: dict) -> dict:
    rows = {row["id"]: row for row in record["dataset"]["cases"]}
    seen = set()
    agents = {}
    for item in record["output_items"]:
        row_id = item["datasource_item"]["id"]
        if row_id not in rows or row_id in seen:
            raise ValueError("Comparison contains unexpected or duplicate decisions")
        seen.add(row_id)
        row = rows[row_id]
        result, anchors = judgments(item, row_id)
        key = f"{row['agent']['name']}:v{row['agent']['version']}"
        agent = agents.setdefault(key, {"passed": 0, "failed": 0, "cases": {}})
        agent["passed" if result["passed"] else "failed"] += 1
        agent["cases"][row["case_id"]] = {
            "passed": result["passed"], "reason": result.get("reason"),
            **{field: row[field] for field in ("source_run_id", "source_response_id") if field in row},
        }
        if anchors is not None:
            agent["cases"][row["case_id"]]["anchor_score"] = anchors["score"]
        if record.get("evaluator", {}).get("kind") == "rubric":
            score = result.get("score")
            if type(score) not in (int, float) or not 0 <= score <= 1:
                raise ValueError(f"{row_id}: missing or invalid normalized rubric score")
            properties = result.get("properties")
            if not isinstance(properties, dict) or not properties.get("dimension_scores"):
                raise ValueError(f"{row_id}: missing rubric dimension scores")
            agent["cases"][row["case_id"]].update(
                score=score, properties=properties,
            )
    if seen != set(rows):
        raise ValueError("Comparison is missing Foundry decisions")
    return {
        "agents": agents,
        "note": "Counts summarize Foundry business judgments, not local grading. "
                "A single diagnostic run does not establish general superiority or isolate causality.",
    }


def verify_regression(record: dict) -> dict:
    expected = {row["id"]: row["expected_pass"] for row in record["dataset"]["cases"]}
    decisions = {}
    for item in record["output_items"]:
        case_id = item["datasource_item"]["id"]
        if case_id not in expected or case_id in decisions:
            raise ValueError(f"Unexpected or duplicate regression output: {case_id}")
        if item.get("status") != "completed" or len(item["results"]) != 1:
            raise ValueError(f"{case_id}: incomplete evaluator result")
        result = item["results"][0]
        if result.get("status") != "completed" or type(result.get("passed")) is not bool:
            raise ValueError(f"{case_id}: evaluator failed or returned no boolean decision")
        decisions[case_id] = {
            "expected_pass": expected[case_id], "actual_pass": result["passed"],
            "matches": result["passed"] == expected[case_id], "reason": result.get("reason"),
        }
    if set(decisions) != set(expected):
        raise ValueError("Regression has missing Foundry decisions")
    return {
        "passed": all(item["matches"] for item in decisions.values()),
        "cases": decisions,
        "note": "Checks Foundry decisions against calibration labels only; no local answer grading.",
    }
