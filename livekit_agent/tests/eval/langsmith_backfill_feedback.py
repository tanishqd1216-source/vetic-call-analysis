"""Supplementary to langsmith_backfill.py: attaches Feedback (score/value) to every already-
backfilled run, since the Experiments UI's pass/fail columns read Feedback, not a run's raw
`outputs` JSON (which langsmith_backfill.py already wrote, but that alone doesn't populate the
comparison table). Reuses the exact same run-id derivation and source-loading logic as
langsmith_backfill.py so it doesn't need to read anything back from LangSmith first -- pure writes.

Emits:
  - one "verdict" feedback per run: score 1.0/0.0 for PASS/FAIL, value=the verdict string,
    comment=judge_reasoning (truncated).
  - for classification_100-family rows (all of legacy/prefix/round2/final; not weakened/flawed,
    which are the curated golden set with per-case-varying rules_in_scope): a
    "CLASSIFICATION_COVERAGE" feedback always, and an "EMPATHY_ACKNOWLEDGMENT" feedback for the
    milestones where that rubric tag existed (prefix/round2/final -- NOT legacy, which predates it).
"""
import argparse

from langsmith import Client

from langsmith_backfill import (
    MILESTONE_FILES, run_id_for, load_wrapped, load_plain_list, backfill_final_from_postgres,
)


def rule_passed(row: dict, rule: str) -> bool:
    return rule not in (row.get("violated_rules") or [])


def emit_feedback(client: Client, direction: str, milestone: str, row: dict,
                   case_family: str, dry_run: bool) -> None:
    case_id = row["case_id"]
    run_id = run_id_for(direction, milestone, case_id)
    verdict = row.get("verdict")

    if dry_run:
        return

    client.create_feedback(
        run_id=run_id,
        key="verdict",
        score=1.0 if verdict == "PASS" else 0.0,
        value=verdict,
        comment=(row.get("judge_reasoning") or "")[:2000],
    )

    if case_family == "classification_100":
        client.create_feedback(
            run_id=run_id,
            key="CLASSIFICATION_COVERAGE",
            score=1.0 if rule_passed(row, "CLASSIFICATION_COVERAGE") else 0.0,
        )
        if milestone != "legacy":  # EMPATHY_ACKNOWLEDGMENT didn't exist at legacy-capture time
            client.create_feedback(
                run_id=run_id,
                key="EMPATHY_ACKNOWLEDGMENT",
                score=1.0 if rule_passed(row, "EMPATHY_ACKNOWLEDGMENT") else 0.0,
            )


def process_file_milestone(client: Client, milestone: str, direction: str, dry_run: bool) -> None:
    from pathlib import Path
    entry = MILESTONE_FILES[milestone].get(direction)
    if entry is None:
        return
    filename, loader = entry
    path = Path(__file__).resolve().parent / "results" / filename
    if not path.exists():
        print(f"[feedback] MISSING FILE, skipping: {path}")
        return
    rows, _ = loader(path)
    rows = [r for r in rows if r.get("direction", direction) == direction]
    case_family = "classification_100" if milestone in ("legacy", "prefix", "round2") else "golden_curated"
    for row in rows:
        emit_feedback(client, direction, milestone, row, case_family, dry_run)
    print(f"[feedback] {direction}-{milestone}: {'(dry run) would write' if dry_run else 'wrote'} "
          f"feedback for {len(rows)} runs")


def process_final_from_postgres(client: Client, direction: str, dry_run: bool) -> None:
    import datetime
    import psycopg2
    from dotenv import dotenv_values

    dashboard_env = dotenv_values(r"D:\ai-call-agent-master\backend\.env")
    db_url = dashboard_env["DATABASE_URL"]
    run_ids = {"outbound": "cmtenrynd0001v0lfbxtr1e64", "inbound": "cmtens7nt002vv0lfxdlxbs2f"}
    conn = psycopg2.connect(db_url)
    cur = conn.cursor()
    cur.execute(
        'SELECT "caseId", verdict, "violatedRules", "judgeReasoning" FROM "EvalResult" '
        'WHERE "runId" = %s ORDER BY "caseId"', (run_ids[direction],),
    )
    rows = [dict(zip(["case_id", "verdict", "violated_rules", "judge_reasoning"], r)) for r in cur.fetchall()]
    cur.close()
    conn.close()
    for row in rows:
        emit_feedback(client, direction, "final", row, "classification_100", dry_run)
    print(f"[feedback] {direction}-final: {'(dry run) would write' if dry_run else 'wrote'} "
          f"feedback for {len(rows)} runs")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--milestone", choices=["all", "legacy", "prefix", "round2", "weakened", "flawed", "final"],
                         default="all")
    parser.add_argument("--direction", choices=["outbound", "inbound", "both"], default="both")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    client = Client()
    directions = ["outbound", "inbound"] if args.direction == "both" else [args.direction]
    milestones = (["legacy", "prefix", "round2", "weakened", "flawed", "final"]
                  if args.milestone == "all" else [args.milestone])

    for milestone in milestones:
        for direction in directions:
            if milestone == "final":
                process_final_from_postgres(client, direction, args.dry_run)
            else:
                process_file_milestone(client, milestone, direction, args.dry_run)


if __name__ == "__main__":
    main()
