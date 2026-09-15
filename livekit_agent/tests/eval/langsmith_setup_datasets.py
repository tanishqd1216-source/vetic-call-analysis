"""One-time (idempotent) setup: creates the two LangSmith datasets that hold every eval case
this project has — the curated golden set (regression/security/taxonomy/edge_case) plus the
100-case classification sweep per direction — as reference Examples.

Reuses `run_eval.load_golden_set()` for the curated golden set, and the same corrected
expected-team data sources `final_reconciliation_rerun.py` (now archived) originally established
-- `compile_outbound_100.py`/`compile_inbound_100.py`/`grade_legacy_classification.py`, plus the
two still-live `verify_inbound_35_check.py`/`verify_inbound_10_new_check.py` modules -- for the
100-case classification sweep. Does not reimplement case selection.

Example ids are deterministic (uuid5 of "vetic:{direction}:{case_id}"), so rerunning this script
upserts rather than duplicates — safe to run again after adding a new golden case.
"""
import argparse
import uuid

import run_eval

NAMESPACE = uuid.NAMESPACE_URL

DATASET_NAMES = {
    "outbound": "vetic-outbound-eval",
    "inbound": "vetic-inbound-eval",
}


def example_id_for(direction: str, case_id: str) -> str:
    return str(uuid.uuid5(NAMESPACE, f"vetic:{direction}:{case_id}"))


def case_to_example(direction: str, case: dict, case_family: str, source_file: str) -> dict:
    inputs = {
        "case_id": case["id"],
        "direction": direction,
        "case_family": case_family,
        "type": case.get("type"),
        "turn_mode": case.get("turn_mode"),
        "customer_turns": case.get("customer_turns"),
        "opening_issue": case.get("opening_issue"),
        "customer_persona_prompt": case.get("customer_persona_prompt"),
        "rules_in_scope": case.get("rules_in_scope", []),
        "source_query_id": case.get("source_query_id"),
        "simulated_customer": case.get("simulated_customer"),
    }
    outputs = {
        "expected_behavior": case.get("expected_behavior"),
        "expected_category": case.get("expected_category"),
        "expected_subcategory": case.get("expected_subcategory"),
        "expected_team": case.get("expected_team"),
    }
    metadata = {
        "source_file": source_file,
        "provenance": "session-verified" if case_family == "classification_100" else "curated_golden",
    }
    return {
        "id": example_id_for(direction, case["id"]),
        "inputs": inputs,
        "outputs": outputs,
        "metadata": metadata,
    }


def ensure_dataset(client, dataset_name: str, direction: str):
    existing = list(client.list_datasets(dataset_name=dataset_name))
    if existing:
        print(f"[setup] dataset already exists: {dataset_name} ({existing[0].id})")
        return existing[0]
    ds = client.create_dataset(
        dataset_name=dataset_name,
        description=f"Vetic {direction} calling-agent eval cases: curated golden set "
                     f"(regression/security/taxonomy/edge_case) + the 100-case classification sweep.",
        metadata={"direction": direction},
    )
    print(f"[setup] created dataset: {dataset_name} ({ds.id})")
    return ds


def build_case_from_taxonomy_entry(direction: str, idx: int, category, subcategory, department, caller_line) -> dict:
    """Mirrors final_reconciliation_rerun.build_case() but returns the richer dict shape needed
    for expected_category/subcategory/team, not just expected_behavior text."""
    return {
        "id": f"{direction}-classification-{idx:03d}",
        "type": "classification",
        "turn_mode": "llm_customer",
        "opening_issue": caller_line.strip('"'),
        "customer_persona_prompt": (
            "Answer any follow-up or clarifying questions Aarya asks briefly and cooperatively "
            "(e.g. yes/no, a plausible date, 'no the vet did not check up on it') so the call "
            "reaches an actual resolution rather than stalling on details. Once Aarya offers a "
            "resolution, accept it. Do not volunteer other issues."
        ),
        "rules_in_scope": ["CLASSIFICATION_COVERAGE", "EMPATHY_ACKNOWLEDGMENT"],
        "expected_behavior": (
            f"Caller's issue: {caller_line}\n"
            f"EXPECTED classification — Category: {category} | Subcategory: {subcategory} "
            f"| Department: {department}."
        ),
        "expected_category": category,
        "expected_subcategory": subcategory,
        "expected_team": department,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--direction", choices=["outbound", "inbound", "both"], default="both")
    args = parser.parse_args()

    from langsmith import Client
    client = Client()

    directions = ["outbound", "inbound"] if args.direction == "both" else [args.direction]

    # Rebuild the 100-case lists ourselves (richer shape than compute_case_lists' build_case,
    # which only sets expected_behavior) using the SAME index/label logic it already verified.
    from grade_legacy_classification import parse_taxonomy
    from compile_outbound_100 import FIXED_39 as OUT_FIXED_39, REGRESSION_CHECKED_12 as OUT_REG12
    from compile_inbound_100 import (
        REGRESSION_CHECKED_12 as IN_REG12,
        TAXONOMY_LABEL_DISAGREEMENT as IN_TAXDIS,
    )
    from verify_inbound_35_check import TARGET_INDICES as IN_35, expected_for as in35_expected_for
    from verify_inbound_10_new_check import TARGET_INDICES as IN_10NEW, EXPECTED as IN_10NEW_EXPECTED

    taxonomy = parse_taxonomy()

    for direction in directions:
        dataset_name = DATASET_NAMES[direction]
        ensure_dataset(client, dataset_name, direction)

        examples = []

        golden_cases = run_eval.load_golden_set(direction)
        for case in golden_cases:
            examples.append(case_to_example(direction, case, "golden_curated", f"golden_{direction}.yaml"))

        # Full 100-case universe per direction, resolving expected_team across every bucket
        # this session's investigation produced — not just the 88/43 subset that was actively
        # retested, so the dataset can back a full-coverage run AND every Postgres result row
        # (all 100 per direction) has a matching example for the backfill step.
        if direction == "outbound":
            for i in range(1, 101):
                category, subcategory, legacy_department, caller_line = taxonomy[i]
                expected = OUT_FIXED_39.get(i) or OUT_REG12.get(i) or legacy_department
                case = build_case_from_taxonomy_entry(direction, i, category, subcategory, expected, caller_line)
                examples.append(case_to_example(direction, case, "classification_100", f"stress_test.md row {i}"))
        else:
            for i in range(1, 101):
                category, subcategory, legacy_department, caller_line = taxonomy[i]
                if i in IN_TAXDIS:
                    expected = IN_TAXDIS[i][0]
                elif i in IN_REG12:
                    expected = IN_REG12[i]
                elif i in IN_35:
                    expected = in35_expected_for(i)
                elif i in IN_10NEW:
                    expected = IN_10NEW_EXPECTED[i]
                else:
                    expected = legacy_department
                case = build_case_from_taxonomy_entry(direction, i, category, subcategory, expected, caller_line)
                examples.append(case_to_example(direction, case, "classification_100", f"stress_test.md row {i}"))

        result = client.create_examples(dataset_name=dataset_name, examples=examples)
        print(f"[setup] {direction}: upserted {len(examples)} examples into {dataset_name}")
        print(f"[setup] {direction}: response summary: {result if isinstance(result, dict) else 'ok'}")

        count = len(list(client.list_examples(dataset_name=dataset_name)))
        print(f"[setup] {direction}: dataset now has {count} examples total")


if __name__ == "__main__":
    main()
