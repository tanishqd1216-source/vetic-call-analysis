"""Targeted check: re-simulates the 35 inbound categories that are (a) still legacy-FAIL on
inbound and (b) map to a routing bullet already edited this session and live-verified fixed
on outbound. This does NOT assume the outbound fix carries over — the "opd" marker bug earlier
this session proved identical mirrored code can behave differently on inbound (identity
verification changes the turn structure) — so each of these 35 is live-retested against the
CURRENT agent_inbound.py rather than marked PASS by inference from the outbound result.

Expected-behavior text reused as-is from what was already implemented and verified on
outbound: FIXED_39 (compile_outbound_100.py) for team-name cases, and the action-based text
from verify_unaddressed_fixes.py for #9 (grooming reschedule action, no team name required).
"""
import concurrent.futures
import json
from pathlib import Path

import run_eval
from grade_legacy_classification import parse_taxonomy
from compile_outbound_100 import FIXED_39
from verify_unaddressed_fixes import EXPECTED as UNADDRESSED_EXPECTED
import rubric

agent_inbound = run_eval.agent_inbound

HERE = Path(__file__).resolve().parent

TARGET_INDICES = [
    9, 13, 22, 23, 25, 33, 53, 54, 55, 57, 58, 59, 60, 61, 62, 63, 65, 66,
    68, 69, 71, 72, 74, 75, 77, 78, 81, 82, 85, 87, 91, 92, 95, 97, 100,
]

FIXTURE_NAME = "Fixture Customer Two"
FIXTURE_MOBILE = "9000000002"


def expected_for(idx: int) -> str:
    if idx == 9:
        return UNADDRESSED_EXPECTED[9]
    if idx == 100:
        # The taxonomy's opening_issue for this case is a garbled/dropped-call moment with
        # no defined substantive follow-up — under llm_customer mode, the simulated caller
        # improvises a different substantive topic each run once reconnected. A flat "Tech
        # team" expectation unfairly fails the agent when it correctly names a DIFFERENT
        # team for that improvised substantive topic (e.g. Clinic team for a medical-record
        # request) — the actual, fixed requirement is narrower: the call-quality moment
        # itself must be escalated to Tech, regardless of whatever substantive topic follows.
        return (
            "This call opens with a garbled/dropped-call moment, then the customer says a "
            "discount code did not apply to their last order once reconnected. The agent's "
            "resolution_text MUST cover BOTH issues: (1) per the CALL QUALITY RULE, explicitly "
            "escalate the call-quality/dropped-call issue itself to the Tech team, and (2) name "
            "the Finance team for the discount-not-applied issue. FAIL if either the Tech-team "
            "call-quality escalation or the Finance-team discount escalation is missing, vague, "
            "or names the wrong team."
        )
    return f"CORRECTED expected routing team: {FIXED_39[idx]}."


def build_inbound_case(idx: int, category: str, subcategory: str, department: str, caller_line: str) -> dict:
    # #100's taxonomy caller_line is ONLY the garbled/dropped-call moment with no scripted
    # substantive follow-up, so the generic "state your concern" persona instruction below
    # left the LLM-customer free to improvise a different random topic every run (pet
    # behavior / vaccination records / appointment booking across three separate samples)
    # — a test-construction flaw that flips the verdict independently of agent behavior.
    # Scripting a fixed, simple follow-up here makes the case deterministic.
    if idx == 100:
        persona_prompt = (
            f'Give your name "{FIXTURE_NAME}" and mobile number "{FIXTURE_MOBILE}" when asked '
            "to verify your identity. Once reconnected after the dropped-call moment, say: "
            '"I wanted to ask about a discount code that did not apply to my last order." '
            "Answer any follow-up or clarifying questions Aarya asks briefly and cooperatively "
            "so the call reaches an actual resolution rather than stalling on details. Once "
            "Aarya offers a resolution, accept it. Do not volunteer other issues."
        )
    else:
        persona_prompt = (
            f'Give your name "{FIXTURE_NAME}" and mobile number "{FIXTURE_MOBILE}" when asked '
            "to verify your identity, then state your concern above. Answer any follow-up or "
            "clarifying questions Aarya asks briefly and cooperatively so the call reaches an "
            "actual resolution rather than stalling on details. Once Aarya offers a resolution, "
            "accept it. Do not volunteer other issues."
        )
    return {
        "id": f"inbound-classification-{idx:03d}",
        "type": "classification",
        "turn_mode": "llm_customer",
        "opening_issue": caller_line.strip('"'),
        "customer_persona_prompt": persona_prompt,
        "rules_in_scope": ["CLASSIFICATION_COVERAGE"],
        "expected_behavior": expected_for(idx),
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

    out_path = HERE / "results" / "_verify_inbound_35_outbound_shaped.json"
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

    graded_path = HERE / "results" / "_verify_inbound_35_graded.json"
    graded_path.write_text(json.dumps(graded, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n[verify] wrote {out_path}\n[verify] wrote {graded_path}")


if __name__ == "__main__":
    main()
