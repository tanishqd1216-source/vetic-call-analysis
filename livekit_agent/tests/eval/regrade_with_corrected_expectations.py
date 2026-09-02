"""Re-grades the transcripts already captured by verify_routing_fixes.py against the
CORRECT expected team per case — i.e. what was actually implemented in the
RESOLUTION LOOKUP TABLE (per the user's explicit routing decisions), not the original
stress_test.md taxonomy department column that verify_routing_fixes.py mistakenly used
as-is. No new agent/customer calls — only re-runs the judge over existing transcripts.
"""
import json
from pathlib import Path

import run_eval  # noqa: F401  (loads .env / sets up sys.path as a side effect)
import rubric

HERE = Path(__file__).resolve().parent
SRC = HERE / "results" / "_verify_routing_fixes_outbound.json"

# idx -> corrected expected team/behavior, reflecting what was ACTUALLY implemented.
# "unaddressed": True marks cases deliberately left unchanged (Pattern 3 or genuinely
# not covered by the user's instructions) — still graded, but a FAIL here is expected
# and known, not a fix failure.
CORRECTED = {
    9:  dict(team="Clinic (grooming team's own reschedule action, no team named)", unaddressed=True),
    10: dict(team="Clinic (agent's own script says grooming team)", unaddressed=True),
    22: dict(team="Operations team"),
    23: dict(team="Operations team"),
    25: dict(team="Operations team"),
    33: dict(team="Finance team"),
    35: dict(team="Tech team"),
    53: dict(team="Finance team"),
    54: dict(team="Finance team"),
    55: dict(team="Finance team"),
    57: dict(team="Clinic team"),
    58: dict(team="Clinic team"),
    59: dict(team="Clinic team"),
    60: dict(team="Clinic team"),
    61: dict(team="Clinic team"),
    62: dict(team="Clinic team"),
    63: dict(team="Clinic team"),
    64: dict(team="Clinic team"),
    65: dict(team="Clinic team"),
    66: dict(team="Clinic team"),
    68: dict(team="Home Delivery team"),
    69: dict(team="Home Delivery team"),
    71: dict(team="Home Delivery team"),
    72: dict(team="Home Delivery team"),
    74: dict(team="Clinic and Billing team (unaddressed — not in scope)", unaddressed=True),
    75: dict(team="Clinic and Billing team (unaddressed — not in scope)", unaddressed=True),
    77: dict(team="Clinic team (unaddressed — not in scope)", unaddressed=True),
    78: dict(team="Finance team"),
    81: dict(team="Tech team (already covered pre-existing — not a new fix)", unaddressed=True),
    82: dict(team="Tech team"),
    91: dict(team="Home Delivery team"),
    92: dict(team="Home Delivery team"),
    94: dict(team="Home Delivery team"),
    95: dict(team="Home Delivery team"),
    97: dict(team="Home Delivery team"),
    100: dict(team="Tech team"),
}

# 85/87 are policy-content cases, not team-routing cases — graded on whether the agent
# firmly declines cash refund and offers to fulfill the original service instead.
REFUND_SPECIAL = {
    85: "The correct resolution is NOT a team name — it is to firmly state a cash refund "
        "cannot be issued, and instead offer to arrange for the original service/product to "
        "be provided (re-deliver, reschedule, re-perform). FAIL if the agent promises a cash "
        "refund, offers wallet/store credit, or never offers to fulfill the original service.",
    87: "The correct resolution is NOT a team name — it is to firmly state a cash refund "
        "cannot be issued, and instead offer to arrange for the original service/product to "
        "be provided (re-deliver, reschedule, re-perform). FAIL if the agent promises a cash "
        "refund, offers wallet/store credit, or never offers to fulfill the original service.",
}


def idx_of(case_id: str) -> int:
    return int(case_id.split("-")[-1])


def main() -> None:
    results = json.loads(SRC.read_text(encoding="utf-8"))
    out = []
    for r in results:
        idx = idx_of(r["case_id"])
        transcript = r["transcript"]
        if idx in REFUND_SPECIAL:
            case = {
                "id": r["case_id"],
                "type": "classification",
                "rules_in_scope": ["CLASSIFICATION_COVERAGE"],
                "expected_behavior": REFUND_SPECIAL[idx],
            }
        else:
            info = CORRECTED.get(idx)
            team = info["team"] if info else "(no correction defined)"
            case = {
                "id": r["case_id"],
                "type": "classification",
                "rules_in_scope": ["CLASSIFICATION_COVERAGE"],
                "expected_behavior": f"CORRECTED expected routing team: {team}.",
            }
        verdict = rubric.judge_transcript(case, transcript)
        out.append({
            "case_id": r["case_id"],
            "idx": idx,
            "unaddressed": CORRECTED.get(idx, {}).get("unaddressed", False),
            "verdict": verdict["overall"],
            "violated_rules": verdict.get("violated_rules", []),
            "reason": (verdict.get("rule_results") or [{}])[0].get("reason", ""),
        })

    out.sort(key=lambda r: r["idx"])
    fixed_scope = [r for r in out if not r["unaddressed"]]
    unaddressed_scope = [r for r in out if r["unaddressed"]]
    passed_fixed = sum(1 for r in fixed_scope if r["verdict"] == "PASS")
    passed_unaddr = sum(1 for r in unaddressed_scope if r["verdict"] == "PASS")

    print(f"IN-SCOPE FIXES: {passed_fixed}/{len(fixed_scope)} PASS")
    for r in fixed_scope:
        if r["verdict"] != "PASS":
            print(f"  STILL FAIL  {r['case_id']}  {r['reason']}")

    print(f"\nDELIBERATELY UNADDRESSED (known gaps, not in scope): {passed_unaddr}/{len(unaddressed_scope)} PASS")
    for r in unaddressed_scope:
        print(f"  {r['verdict']:5s} {r['case_id']}  {r['reason']}")

    out_path = HERE / "results" / "_regrade_corrected_outbound.json"
    out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nwrote {out_path}")


if __name__ == "__main__":
    main()
