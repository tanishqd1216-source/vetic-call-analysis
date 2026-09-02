"""Verifies the new known-misroute correction guard against the 3 categories it targets
(#59, 78, 89) — each a deterministic (not stochastic) wrong-team pattern that survived
three rounds of static prompt disambiguation. Reuses EXPECTED text from
verify_inbound_10_new_check.py (idx 89) and verify_inbound_35_check.py (idx 59, 78 via
expected_for) rather than inventing new expectations.
"""
import concurrent.futures
import json
from pathlib import Path

import run_eval
from grade_legacy_classification import parse_taxonomy
from verify_inbound_35_check import FIXED_39, FIXTURE_NAME, FIXTURE_MOBILE
from verify_inbound_10_new_check import EXPECTED as EXPECTED_10_NEW
import rubric

agent_inbound = run_eval.agent_inbound

HERE = Path(__file__).resolve().parent

TARGET_INDICES = [59, 78, 89]


def expected(idx: int) -> str:
    if idx == 89:
        return EXPECTED_10_NEW[89]
    return f"CORRECTED expected routing team: {FIXED_39[idx]}."


def build_case(idx: int, category: str, subcategory: str, department: str, caller_line: str) -> dict:
    return {
        "id": f"inbound-classification-{idx:03d}",
        "type": "classification",
        "turn_mode": "llm_customer",
        "opening_issue": caller_line.strip('"'),
        "customer_persona_prompt": (
            f'Give your name "{FIXTURE_NAME}" and mobile number "{FIXTURE_MOBILE}" when asked '
            "to verify your identity, then state your concern above. Answer any follow-up or "
            "clarifying questions Aarya asks briefly and cooperatively so the call reaches an "
            "actual resolution rather than stalling on details. Once Aarya offers a resolution, "
            "accept it. Do not volunteer other issues."
        ),
        "rules_in_scope": ["CLASSIFICATION_COVERAGE"],
        "expected_behavior": expected(idx),
    }


def main() -> None:
    taxonomy = parse_taxonomy()
    cases = [build_case(i, *taxonomy[i]) for i in TARGET_INDICES]

    results = [None] * len(cases)
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
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

    out_path = HERE / "results" / "_verify_inbound_3_misroute_outbound_shaped.json"
    out_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")

    graded = []
    for r in results:
        idx = int(r["case_id"].split("-")[-1])
        case = {
            "id": r["case_id"],
            "type": "classification",
            "rules_in_scope": ["CLASSIFICATION_COVERAGE"],
            "expected_behavior": expected(idx),
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

    graded_path = HERE / "results" / "_verify_inbound_3_misroute_graded.json"
    graded_path.write_text(json.dumps(graded, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n[verify] wrote {out_path}\n[verify] wrote {graded_path}")


if __name__ == "__main__":
    main()
