"""Final reconciliation rerun: makes REAL live simulated calls (run_eval.run_case, real
agent + real gpt-5 judge, no reused transcripts, no hardcoded verdicts) for every case that
has never actually been tested against the current agent:

  - Outbound: all 100 indices EXCEPT the 12 already live-verified via a real regression-sweep
    run (compile_outbound_100.REGRESSION_CHECKED_12) = 88 cases. This includes the 39
    "FIXED_39" cases, whose prior "fix" was a relabeled expected-answer re-grade of an OLD
    transcript, not a fresh call — and the 49 legacy-carried-over cases, never tested at all.
  - Inbound: the 40 "legacy carried over, not re-tested" indices, plus the 3
    "reviewed, not re-tested" taxonomy-label-disagreement indices = 43 cases. Excludes the 12
    regression-checked + 35 + 10 buckets, which already have a real live-verified result.

Also grades EMPATHY_ACKNOWLEDGMENT (new rubric tag) alongside CLASSIFICATION_COVERAGE on
every case in this run, since that rubric tag has never been graded on any case before.

Uses corrected expected-team labels (compile_outbound_100.FIXED_39 and
regrade_with_corrected_expectations.CORRECTED both fixed: "Operations team" -> "Clinic
Operations team", to match what's actually in agent_outbound.py's RESOLUTION LOOKUP TABLE).

Real API calls. Real cost. Writes real verdicts only -- never hardcodes PASS.
"""
import argparse
import concurrent.futures
import json
import time
from pathlib import Path

import run_eval
from grade_legacy_classification import parse_taxonomy
from compile_outbound_100 import FIXED_39, REGRESSION_CHECKED_12 as OUT_REGRESSION_12
from verify_inbound_35_check import TARGET_INDICES as IN_CHECKED_35
from verify_inbound_10_new_check import TARGET_INDICES as IN_CHECKED_10_NEW
from compile_inbound_100 import (
    REGRESSION_CHECKED_12 as IN_REGRESSION_12,
    TAXONOMY_LABEL_DISAGREEMENT as IN_TAXONOMY_DISAGREEMENT,
)

agent_outbound = run_eval.agent_outbound
agent_inbound = run_eval.agent_inbound

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"

RULES_IN_SCOPE = ["CLASSIFICATION_COVERAGE", "EMPATHY_ACKNOWLEDGMENT"]


def build_case(direction: str, idx: int, category: str, subcategory: str, department: str,
               caller_line: str) -> dict:
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
        "rules_in_scope": RULES_IN_SCOPE,
        "expected_behavior": (
            f"Caller's issue: {caller_line}\n"
            f"EXPECTED classification — Category: {category} | Subcategory: {subcategory} "
            f"| Department: {department}."
        ),
    }


def compute_case_lists():
    taxonomy = parse_taxonomy()

    outbound_skip = set(OUT_REGRESSION_12.keys())
    outbound_indices = [i for i in range(1, 101) if i not in outbound_skip]

    inbound_skip = (
        set(IN_REGRESSION_12.keys())
        | set(IN_CHECKED_35)
        | set(IN_CHECKED_10_NEW)
        | set(IN_TAXONOMY_DISAGREEMENT.keys())
    )
    inbound_else = [i for i in range(1, 101) if i not in inbound_skip]
    inbound_indices = inbound_else + list(IN_TAXONOMY_DISAGREEMENT.keys())

    outbound_cases = []
    for i in outbound_indices:
        category, subcategory, legacy_department, caller_line = taxonomy[i]
        expected = FIXED_39.get(i, legacy_department)
        outbound_cases.append(build_case("outbound", i, category, subcategory, expected, caller_line))

    inbound_cases = []
    for i in inbound_indices:
        category, subcategory, legacy_department, caller_line = taxonomy[i]
        if i in IN_TAXONOMY_DISAGREEMENT:
            expected = IN_TAXONOMY_DISAGREEMENT[i][0]
        else:
            expected = legacy_department
        inbound_cases.append(build_case("inbound", i, category, subcategory, expected, caller_line))

    return outbound_cases, inbound_cases


def run_batch(cases: list, direction: str, workers: int) -> list:
    agent = agent_outbound if direction == "outbound" else agent_inbound
    results = [None] * len(cases)
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(run_eval.run_case, case, direction, agent.BASE_RULES, agent.CALL_FLOW): i
            for i, case in enumerate(cases)
        }
        done = 0
        for fut in concurrent.futures.as_completed(futures):
            i = futures[fut]
            try:
                results[i] = fut.result()
            except Exception as e:
                results[i] = {
                    "case_id": cases[i]["id"], "direction": direction, "verdict": "ERROR",
                    "violated_rules": [], "judge_reasoning": str(e), "transcript": "",
                    "turn_count": 0, "input_tokens": 0, "output_tokens": 0, "latency_ms": 0,
                }
            done += 1
            print(f"[{direction}] {done}/{len(cases)} done — {results[i]['case_id']} -> {results[i]['verdict']}")
    return results


def _case_index(case: dict) -> int:
    return int(case["id"].split("-")[-1])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--limit", type=int, default=None, help="Cap cases per direction (debug only).")
    parser.add_argument("--direction", choices=["outbound", "inbound", "both"], default="both")
    parser.add_argument("--indices", type=str, default=None,
                         help="Comma-separated case indices to rerun (e.g. 1,7,14,22). Applies "
                              "to whichever direction(s) --direction selects; indices not present "
                              "in a direction's normal case list are silently skipped for that "
                              "direction. Writes to *_verify_rerun.json instead of the default "
                              "output files so the original baseline results aren't overwritten.")
    args = parser.parse_args()

    outbound_cases, inbound_cases = compute_case_lists()

    if args.indices:
        wanted = {int(x.strip()) for x in args.indices.split(",") if x.strip()}
        outbound_cases = [c for c in outbound_cases if _case_index(c) in wanted]
        inbound_cases = [c for c in inbound_cases if _case_index(c) in wanted]

    if args.limit:
        outbound_cases = outbound_cases[: args.limit]
        inbound_cases = inbound_cases[: args.limit]

    out_filename = "outbound_verify_rerun.json" if args.indices else "outbound_final_rerun.json"
    in_filename = "inbound_verify_rerun.json" if args.indices else "inbound_final_rerun.json"

    print(f"Outbound cases to rerun: {len(outbound_cases)}")
    print(f"Inbound cases to rerun: {len(inbound_cases)}")

    t0 = time.monotonic()

    if args.direction in ("outbound", "both"):
        out_results = run_batch(outbound_cases, "outbound", args.workers)
        out_path = RESULTS / out_filename
        out_path.write_text(json.dumps(out_results, indent=2, ensure_ascii=False), encoding="utf-8")
        passed = sum(1 for r in out_results if r["verdict"] == "PASS")
        empathy_fail = sum(1 for r in out_results if "EMPATHY_ACKNOWLEDGMENT" in r.get("violated_rules", []))
        print(f"\n[outbound] {passed}/{len(out_results)} PASS overall")
        print(f"[outbound] {empathy_fail}/{len(out_results)} FAILED EMPATHY_ACKNOWLEDGMENT specifically")
        print(f"[outbound] wrote {out_path}")

    if args.direction in ("inbound", "both"):
        in_results = run_batch(inbound_cases, "inbound", args.workers)
        in_path = RESULTS / in_filename
        in_path.write_text(json.dumps(in_results, indent=2, ensure_ascii=False), encoding="utf-8")
        passed = sum(1 for r in in_results if r["verdict"] == "PASS")
        empathy_fail = sum(1 for r in in_results if "EMPATHY_ACKNOWLEDGMENT" in r.get("violated_rules", []))
        print(f"\n[inbound] {passed}/{len(in_results)} PASS overall")
        print(f"[inbound] {empathy_fail}/{len(in_results)} FAILED EMPATHY_ACKNOWLEDGMENT specifically")
        print(f"[inbound] wrote {in_path}")

    elapsed = time.monotonic() - t0
    print(f"\nTotal wall time: {elapsed:.1f}s")


if __name__ == "__main__":
    main()
