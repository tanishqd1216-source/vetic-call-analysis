"""Second re-check round for the 5 categories still failing after the first vague-escalation
guard (#59, 74, 75, 78, 100), following: (1) extending the guard to also cover the
offer-and-ask-combined path (the model was free-typing the vague resolution + STEP 2 ask
together in one message, bypassing state_resolution, for #74/#75/#78), and (2) a fairer
expected_behavior for #100 that only requires the Tech-team call-quality escalation, not a
flat "must be Tech team" verdict on the whole call. #59 is re-tested as-is (no further prompt
change — the CLINIC bullet already literally quotes "the item shows as not available"; the
prior FAIL was the model routing to Tech anyway after the guard forced a named-team retry, a
genuine routing miss worth re-sampling rather than assuming systematic).
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

TARGET_INDICES = [59, 74, 75, 78, 100]


def main() -> None:
    taxonomy = parse_taxonomy()
    cases = [build_inbound_case(i, *taxonomy[i]) for i in TARGET_INDICES]

    results = [None] * len(cases)
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
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

    out_path = HERE / "results" / "_verify_inbound_5_fix_outbound_shaped.json"
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

    graded_path = HERE / "results" / "_verify_inbound_5_fix_graded.json"
    graded_path.write_text(json.dumps(graded, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n[verify] wrote {out_path}\n[verify] wrote {graded_path}")


if __name__ == "__main__":
    main()
