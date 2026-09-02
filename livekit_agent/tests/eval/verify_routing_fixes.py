"""Live-simulates the 39 previously-failed outbound classification cases against the
CURRENT (just-edited) agent_outbound.py, to confirm the RESOLUTION LOOKUP TABLE routing
additions actually fix them — unlike grade_legacy_classification.py, which only re-grades
the frozen 2026-08-12 transcript text and can never reflect a prompt change.

Reuses run_eval.run_case() directly (same simulation path as the golden-set cases:
real tool-calling, real guard logic) with synthetic single-turn "classification" cases.
"""
import argparse
import concurrent.futures
import json
from pathlib import Path

import run_eval
from grade_legacy_classification import parse_taxonomy

agent_outbound = run_eval.agent_outbound

HERE = Path(__file__).resolve().parent

FAILED_INDICES = [
    9, 10, 13, 22, 23, 25, 33, 35, 53, 54, 55, 57, 58, 59, 60, 61, 62, 63, 64, 65, 66,
    68, 69, 71, 72, 74, 75, 77, 78, 81, 82, 85, 87, 91, 92, 94, 95, 97, 100,
]


def build_case(idx: int, category: str, subcategory: str, department: str, caller_line: str) -> dict:
    return {
        "id": f"outbound-classification-{idx:03d}",
        "type": "classification",
        "turn_mode": "llm_customer",
        "opening_issue": caller_line.strip('"'),
        "customer_persona_prompt": (
            "Answer any follow-up or clarifying questions Aarya asks briefly and cooperatively "
            "(e.g. yes/no, a plausible date, 'no the vet did not check up on it') so the call "
            "reaches an actual resolution rather than stalling on details. Once Aarya offers a "
            "resolution, accept it. Do not volunteer other issues."
        ),
        "rules_in_scope": ["CLASSIFICATION_COVERAGE"],
        "expected_behavior": (
            f"Caller's issue: {caller_line}\n"
            f"EXPECTED classification — Category: {category} | Subcategory: {subcategory} "
            f"| Department: {department}."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()

    taxonomy = parse_taxonomy()
    indices = FAILED_INDICES[: args.limit] if args.limit else FAILED_INDICES
    cases = [build_case(i, *taxonomy[i]) for i in indices]

    results = [None] * len(cases)
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(run_eval.run_case, case, "outbound", agent_outbound.BASE_RULES, agent_outbound.CALL_FLOW): i
            for i, case in enumerate(cases)
        }
        done = 0
        for fut in concurrent.futures.as_completed(futures):
            i = futures[fut]
            results[i] = fut.result()
            done += 1
            print(f"[verify] {done}/{len(cases)} done")

    passed = sum(1 for r in results if r["verdict"] == "PASS")
    failed = [r for r in results if r["verdict"] != "PASS"]
    print(f"\n[verify] {passed}/{len(results)} PASS, {len(failed)} still FAIL/ERROR")
    for r in failed:
        print(f"  {r['verdict']:5s} {r['case_id']}  violated={r['violated_rules']}")

    out_path = HERE / "results" / "_verify_routing_fixes_outbound.json"
    out_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[verify] wrote {out_path}")


if __name__ == "__main__":
    main()
