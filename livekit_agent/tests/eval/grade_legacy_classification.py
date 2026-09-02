"""Retroactively grades the pre-existing 100-query classification stress-test captures
(tests/results/stress_test_results_full_gpt-4o-mini.md for outbound,
tests/results/stress_test_results_inbound.md Part A for inbound) using the current
CLASSIFICATION_COVERAGE rubric tag — WITHOUT re-running the live agent.

These captures are dated 2026-08-12, predate this eval harness and every business-rule
fix made since, and were never graded (raw Category/Resolution capture only). This
script judges the already-captured transcripts as-is against each query's expected
Category/Subcategory/Department from tests/stress_test.md, and posts the result as a
new EvalRun under a "legacy-capture" PromptVersion — kept clearly separate from the
"v1-baseline" live-prompt runs, since the exact prompt text that produced these
transcripts is not recoverable.

Only Part A (the 100-query taxonomy sweep) is graded. Inbound Part B (identity-
verification edge cases) is skipped: those 6 scenarios were already adapted into
golden_inbound.yaml's edge_case cases and are covered by the real v1-baseline runs.
"""
import argparse
import concurrent.futures
import hashlib
import json
import re
from pathlib import Path

import rubric
from openai import OpenAI

HERE = Path(__file__).resolve().parent
RESULTS_SRC = HERE.parent / "results"
STRESS_TEST_MD = HERE.parent / "stress_test.md"

CAPTURE_LABEL = "legacy-stress-test-2026-08-12"
CAPTURE_NOTE = (
    "Retroactively graded from a pre-existing raw capture dated 2026-08-12 "
    "(tests/results/stress_test_results_full_gpt-4o-mini.md / "
    "stress_test_results_inbound.md), predating this eval harness and every "
    "business-rule fix made since. The exact BASE_RULES/CALL_FLOW that produced "
    "these transcripts is not recoverable, so this is NOT comparable to the "
    "v1-baseline live-prompt runs — treat it as a point-in-time coverage snapshot, "
    "not a current-state result. Graded (no new agent calls) against the "
    "CLASSIFICATION_COVERAGE rubric tag added for this purpose."
)


def parse_taxonomy() -> dict:
    """Returns {index: (category, subcategory, department, caller_line)} for 1-100."""
    text = STRESS_TEST_MD.read_text(encoding="utf-8")
    taxonomy = {}
    for line in text.splitlines():
        m = re.match(r"^\|\s*(\d+)\s*\|(.+)\|(.+)\|(.+)\|(.+)\|\s*$", line)
        if not m:
            continue
        idx = int(m.group(1))
        caller_line = m.group(2).strip()
        category = m.group(3).strip()
        subcategory = m.group(4).strip()
        department = m.group(5).strip()
        taxonomy[idx] = (category, subcategory, department, caller_line)
    return taxonomy


def parse_transcripts(md_path: Path, stop_marker: str | None = None) -> dict:
    """Returns {index: transcript_text} parsed from a '### Query N: ...' delimited
    'Full Call Transcripts' section. Stops before stop_marker if given (e.g. '## Part B')."""
    text = md_path.read_text(encoding="utf-8")
    anchor = "Full Call Transcripts"
    start = text.find(anchor)
    if start == -1:
        raise ValueError(f"'{anchor}' section not found in {md_path}")
    section = text[start:]
    if stop_marker:
        stop = section.find(stop_marker)
        if stop != -1:
            section = section[:stop]

    blocks = re.split(r"\n(?=### Query \d+:)", section)
    out = {}
    for block in blocks:
        m = re.match(r"### Query (\d+):", block)
        if not m:
            continue
        idx = int(m.group(1))
        body = block[m.end():].strip()
        body = body.rstrip("-").strip()
        out[idx] = body
    return out


def grade_one(client: OpenAI, direction: str, idx: int, category: str, subcategory: str,
              department: str, caller_line: str, transcript: str) -> dict:
    case = {
        "id": f"{direction}-classification-{idx:03d}",
        "type": "classification",
        "rules_in_scope": ["CLASSIFICATION_COVERAGE"],
        "expected_behavior": (
            f"Caller's issue: {caller_line}\n"
            f"EXPECTED classification — Category: {category} | Subcategory: {subcategory} "
            f"| Department: {department}."
        ),
    }
    verdict = rubric.judge_transcript(case, transcript, client=client)
    return {
        "case_id": case["id"],
        "direction": direction,
        "case_type": "classification",
        "verdict": verdict["overall"],
        "violated_rules": verdict.get("violated_rules", []),
        "judge_reasoning": json.dumps(verdict.get("rule_results", [])),
        "transcript": (
            f"[EXPECTED] Category: {category} | Subcategory: {subcategory} | "
            f"Department: {department}\n\n{transcript}"
        ),
        "turn_count": None,
        "input_tokens": None,
        "output_tokens": None,
        "latency_ms": None,
    }


def grade_direction(direction: str, md_path: Path, taxonomy: dict, limit: int | None,
                     workers: int, stop_marker: str | None = None) -> list:
    transcripts = parse_transcripts(md_path, stop_marker=stop_marker)
    indices = sorted(set(taxonomy) & set(transcripts))
    if limit:
        indices = indices[:limit]
    print(f"[grade] {direction}: {len(indices)} cases to grade (of {len(taxonomy)} taxonomy rows, "
          f"{len(transcripts)} transcripts parsed)")

    client = OpenAI()
    results = [None] * len(indices)
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {}
        for i, idx in enumerate(indices):
            category, subcategory, department, caller_line = taxonomy[idx]
            fut = pool.submit(
                grade_one, client, direction, idx, category, subcategory, department,
                caller_line, transcripts[idx],
            )
            futures[fut] = i
        done = 0
        for fut in concurrent.futures.as_completed(futures):
            i = futures[fut]
            results[i] = fut.result()
            done += 1
            if done % 10 == 0 or done == len(indices):
                print(f"[grade] {direction}: {done}/{len(indices)} graded")
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--direction", choices=["outbound", "inbound", "both"], default="both")
    parser.add_argument("--limit", type=int, default=None, help="Cap cases per direction (smoke test).")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--no-post", action="store_true")
    args = parser.parse_args()

    import run_eval  # local import: reuses write_local_artifact / post_to_dashboard as-is

    taxonomy = parse_taxonomy()
    print(f"[grade] parsed {len(taxonomy)} taxonomy rows from stress_test.md")

    directions = ["outbound", "inbound"] if args.direction == "both" else [args.direction]
    for direction in directions:
        md_path = (
            RESULTS_SRC / "stress_test_results_full_gpt-4o-mini.md" if direction == "outbound"
            else RESULTS_SRC / "stress_test_results_inbound.md"
        )
        stop_marker = "## Part B" if direction == "inbound" else None
        results = grade_direction(direction, md_path, taxonomy, args.limit, args.workers, stop_marker)

        passed = sum(1 for r in results if r["verdict"] == "PASS")
        failed = sum(1 for r in results if r["verdict"] == "FAIL")
        errors = sum(1 for r in results if r["verdict"] == "ERROR")
        print(f"[grade] {direction}: {passed}/{len(results)} PASS, {failed} FAIL, {errors} ERROR")
        for r in results:
            if r["verdict"] != "PASS":
                print(f"    {r['verdict']:5s} {r['case_id']}  violated={r['violated_rules']}")

        prompt_hash = hashlib.sha256(f"LEGACY_STRESS_TEST_CAPTURE_2026-08-12::{direction}".encode()).hexdigest()
        run_eval.write_local_artifact(direction, CAPTURE_LABEL, prompt_hash, results)
        if not args.no_post:
            run_eval.post_to_dashboard(direction, CAPTURE_LABEL, prompt_hash, CAPTURE_NOTE,
                                        "legacy-capture", results)


if __name__ == "__main__":
    main()
