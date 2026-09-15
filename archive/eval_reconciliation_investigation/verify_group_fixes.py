"""Live-simulates just the 15 still-failing in-scope routing cases (Groups 1-4 from the
prior verification) against the CURRENT agent_outbound.py, after the QUICK ESCALATIONS
disambiguation rewrite + Step 1C opener wording fix. Reuses build_case()/parse_taxonomy()
from verify_routing_fixes.py and grades against the same CORRECTED expected-team mapping
used in regrade_with_corrected_expectations.py (no new mapping invented here).
"""
import concurrent.futures
import json
from pathlib import Path

import run_eval
from grade_legacy_classification import parse_taxonomy
from verify_routing_fixes import build_case
import rubric

agent_outbound = run_eval.agent_outbound

HERE = Path(__file__).resolve().parent

TARGET_INDICES = [22, 25, 58, 82, 100]

CORRECTED_TEAM = {
    22: "Operations team",
    23: "Operations team",
    25: "Operations team",
    54: "Finance team",
    57: "Clinic team",
    58: "Clinic team",
    59: "Clinic team",
    62: "Clinic team",
    63: "Clinic team",
    64: "Clinic team",
    65: "Clinic team",
    66: "Clinic team",
    82: "Tech team",
    97: "Home Delivery team",
    100: "Tech team",
}


def main() -> None:
    taxonomy = parse_taxonomy()
    cases = [build_case(i, *taxonomy[i]) for i in TARGET_INDICES]

    results = [None] * len(cases)
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
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

    out_path = HERE / "results" / "_verify_group_fixes_outbound.json"
    out_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")

    graded = []
    for r in results:
        idx = int(r["case_id"].split("-")[-1])
        team = CORRECTED_TEAM[idx]
        case = {
            "id": r["case_id"],
            "type": "classification",
            "rules_in_scope": ["CLASSIFICATION_COVERAGE"],
            "expected_behavior": f"CORRECTED expected routing team: {team}.",
        }
        verdict = rubric.judge_transcript(case, r["transcript"])
        graded.append({
            "case_id": r["case_id"],
            "idx": idx,
            "expected_team": team,
            "verdict": verdict["overall"],
            "reason": (verdict.get("rule_results") or [{}])[0].get("reason", ""),
        })

    graded.sort(key=lambda r: r["idx"])
    passed = sum(1 for r in graded if r["verdict"] == "PASS")
    print(f"\n[verify] {passed}/{len(graded)} PASS")
    for r in graded:
        mark = "PASS" if r["verdict"] == "PASS" else "FAIL"
        print(f"  {mark:5s} #{r['idx']:3d} (expected {r['expected_team']:20s}) {r['reason']}")

    graded_path = HERE / "results" / "_verify_group_fixes_graded.json"
    graded_path.write_text(json.dumps(graded, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n[verify] wrote {out_path}\n[verify] wrote {graded_path}")


if __name__ == "__main__":
    main()
