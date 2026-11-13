"""Weighted dimensions for the Foundry-managed business rubric evaluator."""


RUBRIC_EVALUATOR_NAME = "project-planning-business-rubric"


def rubric_definition() -> dict:
    return {
        "type": "rubric",
        "pass_threshold": 0.5,
        "dimensions": [
            {
                "id": "shared_data", "weight": 1, "always_applicable": True,
                "description": "Correctness and completeness of every mandatory criterion labeled "
                "shared_data in the query's reference packet. Check exact numbers, dates, project "
                "identities and document clauses against reference_context and expected_answer. "
                "Accept equivalent wording and formats, not changed meaning.",
            },
            {
                "id": "ontology_policy", "weight": 1, "always_applicable": False,
                "description": "Correctness and completeness of every mandatory criterion labeled "
                "ontology_policy in the reference packet. Check ratings, scope and required "
                "escalation deadlines against the supplied rules, regardless of which agent "
                "produced the answer. Not applicable if the case has no ontology_policy criteria.",
            },
            {
                "id": "uncertainty", "weight": 1, "always_applicable": True,
                "description": "Satisfy every uncertainty criterion in the reference packet. "
                "Distinguish forecasts from actual outcomes, estimates from final prices, "
                "required approval from approval already given, and possible impacts from "
                "confirmed impacts. Do not treat explicitly conditional risks as established facts.",
            },
            {
                "id": "critical_errors", "weight": 1, "always_applicable": True,
                "description": "Avoid every critical error listed in the reference packet and "
                "additional unsupported business claims. Check the actual answer wording against "
                "each error definition. Naming a manager is not evidence that a notification "
                "was sent. Assess only the candidate response, not the reference packet itself.",
            },
            {
                "id": "general_quality", "weight": 1, "always_applicable": True,
                "description": "Other relevant answer-quality issues not covered by the preceding "
                "dimensions: directly address the original question, make reasoning understandable "
                "and identify supporting documents or policies without requiring a citation syntax.",
            },
        ],
    }
