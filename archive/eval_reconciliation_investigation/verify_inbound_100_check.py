"""Verifies #100 after making its test case deterministic (a fixed discount-code follow-up
instead of letting the LLM-customer improvise a random substantive topic each run) and
tightening its expected_behavior to require both the Tech call-quality escalation AND the
Finance discount escalation. Runs it multiple times (N_SAMPLES) since call-quality handling
has shown intermittent behavior before, to confirm the fix is stable, not a one-off pass.
"""
import concurrent.futures
import json
from pathlib import Path

import run_eval
from grade_legacy_classification import parse_taxonomy
from verify_inbound_35_check import build_inbound_case, expected_for
import rubric

agent_inbound = run_eval.agent_inbound

HERE = Path(__file__).resolve().parent

N_SAMPLES = 3


def main() -> None:
    taxonomy = parse_taxonomy()
    case = build_inbound_case(100, *taxonomy[100])
    cases = [case] * N_SAMPLES

    results = [None] * len(cases)
    with concurrent.futures.ThreadPoolExecutor(max_workers=N_SAMPLES) as pool:
        futures = {
            pool.submit(run_eval.run_case, c, "inbound", agent_inbound.BASE_RULES, agent_inbound.CALL_FLOW): i
            for i, c in enumerate(cases)
        }
        done = 0
        for fut in concurrent.futures.as_completed(futures):
            i = futures[fut]
            results[i] = fut.result()
            done += 1
            print(f"[verify] {done}/{len(cases)} done")

    out_path = HERE / "results" / "_verify_inbound_100_outbound_shaped.json"
    out_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")

    graded = []
    for i, r in enumerate(results):
        c = {
            "id": f"{r['case_id']}-sample{i+1}",
            "type": "classification",
            "rules_in_scope": ["CLASSIFICATION_COVERAGE"],
            "expected_behavior": expected_for(100),
        }
        verdict = rubric.judge_transcript(c, r["transcript"])
        graded.append({
            "sample": i + 1,
            "verdict": verdict["overall"],
            "reason": (verdict.get("rule_results") or [{}])[0].get("reason", ""),
        })

    passed = sum(1 for r in graded if r["verdict"] == "PASS")
    print(f"\n[verify] {passed}/{len(graded)} PASS")
    for r in graded:
        mark = "PASS" if r["verdict"] == "PASS" else "FAIL"
        print(f"  {mark:5s} sample {r['sample']}  {r['reason']}")

    graded_path = HERE / "results" / "_verify_inbound_100_graded.json"
    graded_path.write_text(json.dumps(graded, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n[verify] wrote {out_path}\n[verify] wrote {graded_path}")


if __name__ == "__main__":
    main()
