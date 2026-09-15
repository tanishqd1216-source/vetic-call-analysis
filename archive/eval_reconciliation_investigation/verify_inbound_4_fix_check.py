"""Third re-check round for the 4 categories still failing after rounds 1-2 (#59, 74, 78,
100), following: (1) removing the FINANCE/CLINIC-AND-BILLING duplicate claim on "discount
not applied" (a genuine prompt-authoring conflict, not a model failure — both bullets
literally claimed the same trigger phrase), (2) disambiguating CLINIC AND BILLING's
"wrong selling" from CLINIC's plain membership-coverage bullet, (3) excluding product
availability from TECH's EMR/invoice "not visible" bullet, and (4) a new proactive
whole-conversation guard that forces the Tech-team call-quality escalation before the call
closes if an early garbled/dropped-call signal was never followed up on. Reuses
build_inbound_case()/expected_for() from verify_inbound_35_check.py.
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

TARGET_INDICES = [59, 74, 78, 100]


def main() -> None:
    taxonomy = parse_taxonomy()
    cases = [build_inbound_case(i, *taxonomy[i]) for i in TARGET_INDICES]

    results = [None] * len(cases)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = {
            pool.submit(run_eval.run_case, case, "inbound", agent_inbound.BASE_RULES, agent_inbound.CALL_FLOW): i
            for i, case in enumerate(cases)
        }
        done = 0
        for fut in concurrent.futures.as_completed(futures):
            i = futures[fut]
            results[i] = fut.result()
            done += 1
            print(f"[verify] {done}/{len(cases)} done")

    out_path = HERE / "results" / "_verify_inbound_4_fix_outbound_shaped.json"
    out_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")

    graded = []
    for r in results:
        idx = int(r["case_id"].split("-")[-1])
        case = {
            "id": r["case_id"],
            "type": "classification",
            "rules_in_scope": ["CLASSIFICATION_COVERAGE"],
            "expected_behavior": expected_for(idx),
        }
        verdict = rubric.judge_transcript(case, r["transcript"])
        graded.append({
            "case_id": r["case_id"],
            "idx": idx,
            "verdict": verdict["overall"],
            "reason": (verdict.get("rule_results") or [{}])[0].get("reason", ""),
        })

    graded.sort(key=lambda r: r["idx"])
    passed = sum(1 for r in graded if r["verdict"] == "PASS")
    print(f"\n[verify] {passed}/{len(graded)} PASS")
    for r in graded:
        mark = "PASS" if r["verdict"] == "PASS" else "FAIL"
        print(f"  {mark:5s} #{r['idx']:3d}  {r['reason']}")

    graded_path = HERE / "results" / "_verify_inbound_4_fix_graded.json"
    graded_path.write_text(json.dumps(graded, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n[verify] wrote {out_path}\n[verify] wrote {graded_path}")


if __name__ == "__main__":
    main()
