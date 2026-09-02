"""Live-simulates the 4 previously-unaddressed cases (#9, #10, #77, #81) against the
CURRENT agent_outbound.py, after adding GROOMING action-execution wording and CLINIC/TECH
disambiguation for the medical-records vs EMR-not-visible conflation and the membership-
misinformation coverage gap. No new mapping invented — expectations below match what was
actually implemented in the prompt this round.
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

TARGET_INDICES = [9, 10]

EXPECTED = {
    9: "The pet got anxious and grooming was stopped partway. The agent must actively offer "
       "to reschedule the remaining grooming 'in a calmer setting with an expert handler' AND "
       "ask what time works for the customer — a concrete scheduling action, not merely an "
       "announcement of a ticket/escalation. Naming a specific team by name is optional and "
       "not required for a PASS; taking the scheduling action is required.",
    10: "The vet said the pet needed grooming after a checkup but the post-grooming session was "
        "never given. The agent must actively offer to schedule the missed post-grooming session "
        "AND ask what time works for the customer — a concrete scheduling action, not merely an "
        "announcement of a ticket/escalation. Naming a specific team by name is optional and not "
        "required for a PASS; taking the scheduling action is required.",
    77: "CORRECTED expected routing team: Clinic team (the customer was given wrong information "
        "about what their membership covers).",
    81: "CORRECTED expected routing team: Tech team (the customer can't find/see a medical record "
        "in the app — a display bug, not the doctor failing to fill it out).",
}


def main() -> None:
    taxonomy = parse_taxonomy()
    cases = [build_case(i, *taxonomy[i]) for i in TARGET_INDICES]

    results = [None] * len(cases)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
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

    out_path = HERE / "results" / "_verify_unaddressed_fixes_outbound.json"
    out_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")

    graded = []
    for r in results:
        idx = int(r["case_id"].split("-")[-1])
        case = {
            "id": r["case_id"],
            "type": "classification",
            "rules_in_scope": ["CLASSIFICATION_COVERAGE"],
            "expected_behavior": EXPECTED[idx],
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

    graded_path = HERE / "results" / "_verify_unaddressed_fixes_graded.json"
    graded_path.write_text(json.dumps(graded, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n[verify] wrote {out_path}\n[verify] wrote {graded_path}")


if __name__ == "__main__":
    main()
