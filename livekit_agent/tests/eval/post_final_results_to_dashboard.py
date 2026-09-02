"""Posts the final compiled 100-case results (both directions) to the ai-call-agent-master
dashboard as new EvalRuns, using the CURRENT live prompt (post all this session's routing
fixes) as the PromptVersion — a new prompt version, since BASE_RULES/CALL_FLOW have changed
substantially from the original v1-baseline already in the dashboard.

Merges each case's transcript/tokens/latency from the most recently-captured raw result
file for that case_id this session, falling back to the 2026-08-12 legacy capture for rows
that were reviewed/carried over rather than re-tested this session — never fabricates a
transcript; the compiled file's verdict/judge_reasoning (already reconciled across every
verification round) is always authoritative over whatever the raw record's OWN verdict says.
"""
import json
from pathlib import Path

import run_eval

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"

agent_outbound = run_eval.agent_outbound
agent_inbound = run_eval.agent_inbound

# Chronological (oldest -> newest) raw-result source files per direction. Later files
# override earlier ones for the same case_id, so the merge keeps the most recently
# captured transcript for each case.
OUTBOUND_SOURCES = [
    "outbound_legacy-stress-test-2026-08-12_20260826T142747.json",
    # NOTE: _outbound_fail_39_full.json deliberately excluded — it's a summary file
    # (idx/category/actual/reason), not a raw transcript capture; no case_id/transcript.
    "_verify_routing_fixes_outbound.json",
    "_regrade_corrected_outbound.json",
    "_verify_group_fixes_outbound.json",
    "_verify_unaddressed_fixes_outbound.json",
    "_verify_originally_passing_outbound.json",
]
INBOUND_SOURCES = [
    "inbound_legacy-stress-test-2026-08-12_20260826T142903.json",
    "_verify_inbound_regression_outbound_shaped.json",
    "_verify_inbound_35_outbound_shaped.json",
    "_verify_inbound_7_fix_outbound_shaped.json",
    "_verify_inbound_5_fix_outbound_shaped.json",
    "_verify_inbound_4_fix_outbound_shaped.json",
    "_verify_inbound_10_new_outbound_shaped.json",
    "_verify_inbound_3_misroute_outbound_shaped.json",
    "_verify_inbound_100_outbound_shaped.json",
]


def load_rows(filename: str) -> list:
    data = json.loads((RESULTS / filename).read_text(encoding="utf-8"))
    return data["results"] if isinstance(data, dict) and "results" in data else data


def build_transcript_index(sources: list) -> dict:
    """Returns {case_id: (row, source_filename)} — source_filename tracked so
    build_results can flag when a "live-verified" case's transcript could only be
    recovered from the legacy (pre-fix) file, e.g. because a later verification round's
    result file was overwritten by a smaller re-run during debugging (a known issue
    this session — several 12/35-case check files got shrunk to 1-2 rows by later,
    narrower re-runs sharing the same output path)."""
    by_case_id = {}
    for filename in sources:
        path = RESULTS / filename
        if not path.exists():
            print(f"[post] WARNING: source file missing, skipped: {filename}")
            continue
        for row in load_rows(filename):
            # Some files sharing this naming convention (e.g. _regrade_corrected_outbound.json)
            # are actually re-grading summaries (case_id/verdict/reason) without a raw
            # transcript capture — skip those rows rather than crash or store a useless entry.
            if "case_id" not in row or "transcript" not in row:
                continue
            by_case_id[row["case_id"]] = (row, filename)
    return by_case_id


def build_results(direction: str, compiled_rows: list, index: dict, legacy_filename: str) -> list:
    results = []
    missing = []
    stale_for_live = []
    for row in compiled_rows:
        idx = row["idx"]
        case_id = f"{direction}-classification-{idx:03d}"
        entry = index.get(case_id)
        is_live_verified = row["verification"].startswith("live-verified")
        if entry is None:
            missing.append(idx)
            transcript = "(no captured transcript found for this case)"
            turn_count = input_tokens = output_tokens = latency_ms = 0
        else:
            raw, source_filename = entry
            if is_live_verified and source_filename == legacy_filename:
                stale_for_live.append(idx)
            transcript = raw["transcript"]
            turn_count = raw.get("turn_count") or 0
            input_tokens = raw.get("input_tokens") or 0
            output_tokens = raw.get("output_tokens") or 0
            latency_ms = raw.get("latency_ms") or 0
        results.append({
            "case_id": case_id,
            "case_type": "classification",
            "verdict": row["verdict"],
            "violated_rules": [] if row["verdict"] == "PASS" else ["CLASSIFICATION_COVERAGE"],
            "judge_reasoning": row.get("judge_reasoning") or row.get("verification") or "",
            "transcript": transcript,
            "turn_count": turn_count,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "latency_ms": latency_ms,
        })
    if missing:
        print(f"[post] {direction}: no transcript found for {len(missing)} cases: {missing}")
    if stale_for_live:
        print(f"[post] {direction}: WARNING — {len(stale_for_live)} cases marked "
              f"live-verified-this-session but only the STALE pre-fix legacy transcript "
              f"survives (the live round's result file was later overwritten by a "
              f"smaller re-run): {stale_for_live}")
    return results


def main() -> None:
    for direction, agent_module, sources, compiled_filename in [
        ("outbound", agent_outbound, OUTBOUND_SOURCES, "outbound_100_compiled.json"),
        ("inbound", agent_inbound, INBOUND_SOURCES, "inbound_100_compiled.json"),
    ]:
        compiled_rows = json.loads((RESULTS / compiled_filename).read_text(encoding="utf-8"))
        index = build_transcript_index(sources)
        results = build_results(direction, compiled_rows, index, legacy_filename=sources[0])

        prompt_hash = run_eval.compute_prompt_hash(agent_module.BASE_RULES, agent_module.CALL_FLOW)
        prompt_text = agent_module.BASE_RULES + "\n\n" + agent_module.CALL_FLOW

        passed = sum(1 for r in results if r["verdict"] == "PASS")
        print(f"[post] {direction}: {passed}/{len(results)} PASS — posting to dashboard...")

        run_eval.post_to_dashboard(
            direction=direction,
            label="v2-post-routing-fixes-final",
            prompt_hash=prompt_hash,
            prompt_text=prompt_text,
            source_kind="baseline",
            results=results,
        )


if __name__ == "__main__":
    main()
