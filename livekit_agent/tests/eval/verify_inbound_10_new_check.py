"""Live-checks the 10 categories diagnosed as fixable from the 13 untested-inbound-FAIL
set: 6 zero-coverage categories that fell to a generic DEFAULT escalation because no bullet
existed for them at all (#28, 31, 32 -> new CLINIC OPERATIONS bullets; #48, 49, 90 -> new
HOME DELIVERY bullets), and 4 categories where an exact-matching bullet already existed but
the model still misrouted or fell generic anyway (#46, 47 -> HOME DELIVERY's quantity-issue
trigger split out and sharpened; #84 -> TECH's notification bullet given an explicit repeat-
notification example; #89 -> FINANCE's payment-not-reflecting bullet given an explicit
anti-Tech disambiguation for "the app isn't reflecting my payment" phrasing).

Excludes #6, #7, #16 — diagnosed as likely taxonomy/prompt label disagreements (the model's
answer is plausible; the taxonomy's own department label looks like the odd one out), not
real defects, so not touched or re-tested here.
"""
import concurrent.futures
import json
from pathlib import Path

import run_eval
from grade_legacy_classification import parse_taxonomy
import rubric

agent_inbound = run_eval.agent_inbound

HERE = Path(__file__).resolve().parent

FIXTURE_NAME = "Fixture Customer Two"
FIXTURE_MOBILE = "9000000002"

TARGET_INDICES = [28, 31, 32, 46, 47, 48, 49, 84, 89, 90]

EXPECTED = {
    28: "CORRECTED expected routing team: Clinic Operations team (the booked doctor/provider "
        "was not available when the customer arrived at the clinic).",
    31: "CORRECTED expected routing team: Clinic Operations team (unhygienic/unclean clinic "
        "premises reported from an in-person visit).",
    32: "CORRECTED expected routing team: Clinic Operations team (staff tried to oversell/"
        "upsell packages or food during an in-person visit).",
    46: "CORRECTED expected routing team: Home Delivery team (a partial order — part of a "
        "sub-order is missing).",
    47: "CORRECTED expected routing team: Home Delivery team (the customer received less "
        "quantity than they ordered).",
    48: "CORRECTED expected routing team: Home Delivery team (the delivery process itself "
        "was handled poorly — too many calls / no clear updates).",
    49: "CORRECTED expected routing team: Home Delivery team (a promised free gift with the "
        "order never arrived).",
    84: "CORRECTED expected routing team: Tech team (the same app notification/reminder "
        "keeps repeating).",
    89: "CORRECTED expected routing team: Finance team (customer already paid but the app "
        "isn't reflecting the payment) — NOT the Tech team, even though the customer "
        "mentions \"the app.\"",
    90: "CORRECTED expected routing team: Home Delivery team (a cash-on-delivery payment "
        "problem specific to that delivery).",
}


def build_inbound_case(idx: int, category: str, subcategory: str, department: str, caller_line: str) -> dict:
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
        "expected_behavior": EXPECTED[idx],
    }


def main() -> None:
    taxonomy = parse_taxonomy()
    cases = [build_inbound_case(i, *taxonomy[i]) for i in TARGET_INDICES]

    results = [None] * len(cases)
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
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

    out_path = HERE / "results" / "_verify_inbound_10_new_outbound_shaped.json"
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

    graded_path = HERE / "results" / "_verify_inbound_10_new_graded.json"
    graded_path.write_text(json.dumps(graded, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n[verify] wrote {out_path}\n[verify] wrote {graded_path}")


if __name__ == "__main__":
    main()
