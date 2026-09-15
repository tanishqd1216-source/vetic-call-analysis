"""Targeted regression check: re-simulates only the 12 originally-passing legacy cases whose
matching prompt bullet was directly edited today (CLINIC medical-records split, CLINIC
OPERATIONS vs OPERATIONS disambiguation, TECH visibility bullets, HOME DELIVERY catch-all
narrowing, the lab->Clinic reassignment). NOT a full-suite run — a risk-targeted subset chosen
by cross-referencing stress_test.md against exactly what changed in agent_outbound.py today.
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

TARGET_INDICES = [17, 21, 26, 27, 29, 30, 67, 79, 80, 93, 96, 98]

EXPECTED = {
    17: "The doctor never filled in the medical record after the pet's visit (a clinical "
        "process failure, not an app display issue). The agent should route this to the CLINIC "
        "team and say the medical records team will update and share the correct records — "
        "NOT the Tech team.",
    21: "The appointment was booked with the wrong date/time entirely and the customer wants it "
        "corrected. The agent should directly ask for the correct date, time, and service and "
        "confirm the rebooking (the 'Appointment was booked incorrectly' resolution path) — "
        "NOT escalate this to the Tech team as an app-display issue.",
    26: "Long wait time (almost two hours) during an in-person clinic visit. The agent should "
        "escalate this as HIGH PRIORITY to the Clinic Operations team (management reviews "
        "internally) — NOT the Operations team (which is for phone/booking-call issues).",
    27: "A receptionist was rude to the customer DURING an in-person clinic visit. The agent "
        "should escalate this as HIGH PRIORITY to the Clinic Operations team — NOT the "
        "Operations team, which is specifically for rudeness during a PHONE or booking call, "
        "not an in-person clinic visit.",
    29: "The clinic didn't explain procedure details before starting it, during an in-person "
        "visit. The agent should escalate this as HIGH PRIORITY to the Clinic Operations team.",
    30: "The pet was handled carelessly by staff INSIDE the clinic (an in-person visit). The "
        "agent should escalate this as HIGH PRIORITY to the Clinic Operations team.",
    67: "An internal lab report is delayed beyond the normal turnaround time. Consistent with "
        "all other lab-related issues (delays, errors, resampling, X-ray interpretation), this "
        "should route to the CLINIC team by name — there is no separate lab or ops team for "
        "this in the current routing.",
    79: "The customer's active membership isn't visible anywhere in the app — a display/technical "
        "bug. The agent should route this to the Tech team.",
    80: "The customer's latest invoice isn't showing up in the app at all — a display/technical "
        "bug (the invoice not LOADING), not a request to understand or clarify a charge. The "
        "agent should route this to the Tech team, NOT the Finance team.",
    93: "The app shows a pharmacy order as delivered but the customer never received it — an "
        "E-Pharmacy delivery issue. Consistent with the other E-Pharmacy cases (return, damaged, "
        "delay, short quantity), this should route to the Home Delivery team.",
    96: "Multi-intent: the customer's delivery never arrived AND they were double charged for it. "
        "The agent should address BOTH — name the Home Delivery team for the missing delivery, "
        "and separately acknowledge/escalate the double charge (Finance team) — not drop either "
        "issue or only handle one of the two.",
    98: "Angry escalation: the customer is calling for the third time about the same rude staff "
        "member during an in-person clinic visit and demands a manager now. The agent should "
        "de-escalate calmly and still name the Clinic Operations team (HIGH PRIORITY) as the "
        "correct escalation path — NOT the Operations team.",
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

    out_path = HERE / "results" / "_verify_originally_passing_outbound.json"
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

    graded_path = HERE / "results" / "_verify_originally_passing_graded.json"
    graded_path.write_text(json.dumps(graded, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n[verify] wrote {out_path}\n[verify] wrote {graded_path}")


if __name__ == "__main__":
    main()
