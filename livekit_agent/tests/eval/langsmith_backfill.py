"""One-time backfill of 6 historical eval milestones into LangSmith, as separate Experiments
(projects linked to the datasets langsmith_setup_datasets.py created). Never asserts more
timing precision than actually exists in the source data -- see TIMING_NOTE below.

Milestones, per the approved migration plan:
  1. legacy    - the original 2026-08-12 taxonomy grading (pre-corrected labels)
  2. prefix    - this session's real pre-fix baseline live rerun (outbound_final_rerun.json /
                 inbound_final_rerun.json)
  3. round2    - the round-2 post-fix verification rerun (outbound_verify_rerun.json /
                 inbound_verify_rerun.json) -- round-1's file state was overwritten and is not
                 recoverable
  4. weakened  - outbound-only deliberate negative control (NO_ETA rule weakened)
  5. flawed    - the early "100% pass" compiled report later found to contain hardcoded,
                 unverified pass marks -- kept as an explicit artifact of the eval-integrity story,
                 not treated as ground truth
  6. final     - the TRUE final reconciled state, read live from Postgres (both fix rounds +
                 the never-touched-but-legitimate regression-checked buckets, properly merged) --
                 marked canonical
"""
import argparse
import datetime
import json
import os
import uuid
from pathlib import Path

from langsmith import Client

from langsmith_setup_datasets import DATASET_NAMES, example_id_for

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"

TIMING_NOTE_REAL_LATENCY = (
    "start_time/end_time are synthetic: end_time is the source file's batch-completion "
    "timestamp (not per-case), start_time = end_time - latency_ms. latency_ms itself IS a real "
    "per-case duration from an actual simulated call."
)
TIMING_NOTE_NO_LATENCY = (
    "start_time = end_time = the source file's batch-completion timestamp. No real per-case "
    "timing exists for this row (legacy retroactive grading or a carried-over/never-refreshed "
    "result) -- do not read this as an instantaneous call."
)


def get_client() -> Client:
    return Client()


def run_id_for(direction: str, milestone: str, case_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"vetic:{milestone}:{direction}:{case_id}"))


def backfill_result_row(client: Client, project_name: str, dataset_name: str, direction: str,
                         milestone: str, row: dict, batch_end_time: datetime.datetime,
                         dry_run: bool) -> None:
    case_id = row["case_id"]
    example_id = example_id_for(direction, case_id)
    latency_ms = row.get("latency_ms") or 0
    end_time = batch_end_time
    if latency_ms > 0:
        start_time = end_time - datetime.timedelta(milliseconds=latency_ms)
        timing_note = TIMING_NOTE_REAL_LATENCY
    else:
        start_time = end_time
        timing_note = TIMING_NOTE_NO_LATENCY

    run_id = run_id_for(direction, milestone, case_id)
    outputs = {
        "verdict": row.get("verdict"),
        "violated_rules": row.get("violated_rules", []),
        "judge_reasoning": row.get("judge_reasoning"),
        "transcript": row.get("transcript", ""),
    }
    extra = {
        "metadata": {
            "turn_count": row.get("turn_count"),
            "input_tokens": row.get("input_tokens"),
            "output_tokens": row.get("output_tokens"),
            "latency_ms": latency_ms,
            "timestamp_precision": timing_note,
            "milestone": milestone,
        }
    }

    if dry_run:
        return

    # create_run's start_time must be within ~24h of the real current time (a LangSmith API
    # constraint discovered while backfilling) -- so create with "now", then immediately
    # overwrite with the real historical start_time/end_time via update_run, which has no such
    # restriction.
    now = datetime.datetime.now(datetime.timezone.utc)
    client.create_run(
        id=run_id,
        name=f"{milestone}:{case_id}",
        inputs={"case_id": case_id, "direction": direction},
        run_type="chain",
        project_name=project_name,
        start_time=now,
        end_time=now,
        reference_example_id=example_id,
    )
    client.update_run(
        run_id,
        start_time=start_time,
        end_time=end_time,
        outputs=outputs,
        extra=extra,
        reference_example_id=example_id,
    )


def load_wrapped(path: Path) -> tuple[list[dict], datetime.datetime]:
    """For files written by run_eval.write_local_artifact() -- has a real generatedAt."""
    data = json.loads(path.read_text(encoding="utf-8"))
    end_time = datetime.datetime.fromisoformat(data["generatedAt"])
    return data["results"], end_time


def load_plain_list(path: Path) -> tuple[list[dict], datetime.datetime]:
    """For files written by final_reconciliation_rerun.py -- plain list, no generatedAt;
    file mtime is the best available batch anchor."""
    data = json.loads(path.read_text(encoding="utf-8"))
    end_time = datetime.datetime.fromtimestamp(path.stat().st_mtime, tz=datetime.timezone.utc)
    return data, end_time


def ensure_project(client: Client, project_name: str, dataset_name: str, description: str):
    existing = list(client.list_projects(name=project_name))
    if existing:
        print(f"[backfill] project already exists: {project_name}")
        return existing[0]
    ds = client.read_dataset(dataset_name=dataset_name)
    proj = client.create_project(project_name=project_name, reference_dataset_id=ds.id, description=description)
    print(f"[backfill] created project: {project_name}")
    return proj


def backfill_from_file(client: Client, milestone: str, direction: str, path: Path,
                        loader, dry_run: bool) -> None:
    dataset_name = DATASET_NAMES[direction]
    project_name = f"{direction}-{milestone}"
    rows, end_time = loader(path)
    rows = [r for r in rows if r.get("direction", direction) == direction]
    print(f"[backfill] {project_name}: {len(rows)} rows from {path.name}, batch end_time={end_time.isoformat()}")
    if not dry_run:
        ensure_project(client, project_name, dataset_name,
                        description=f"Backfilled from {path.name}. See plan/CASE_STUDY.md for context.")
    for row in rows:
        backfill_result_row(client, project_name, dataset_name, direction, milestone, row, end_time, dry_run)
    print(f"[backfill] {project_name}: {'(dry run) would write' if dry_run else 'wrote'} {len(rows)} runs")


def backfill_final_from_postgres(client: Client, direction: str, dry_run: bool) -> None:
    import psycopg2
    from dotenv import dotenv_values

    dashboard_env = dotenv_values(r"D:\ai-call-agent-master\backend\.env")
    db_url = dashboard_env["DATABASE_URL"]
    run_ids = {
        "outbound": "cmtenrynd0001v0lfbxtr1e64",
        "inbound": "cmtens7nt002vv0lfxdlxbs2f",
    }
    conn = psycopg2.connect(db_url)
    cur = conn.cursor()
    cur.execute(
        'SELECT "caseId", verdict, "violatedRules", "judgeReasoning", transcript, "turnCount", '
        '"inputTokens", "outputTokens", "latencyMs" '
        'FROM "EvalResult" WHERE "runId" = %s ORDER BY "caseId"',
        (run_ids[direction],),
    )
    cols = ["case_id", "verdict", "violated_rules", "judge_reasoning", "transcript",
            "turn_count", "input_tokens", "output_tokens", "latency_ms"]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    cur.execute('SELECT "completedAt" FROM "EvalRun" WHERE id = %s', (run_ids[direction],))
    end_time = cur.fetchone()[0]
    if end_time.tzinfo is None:
        end_time = end_time.replace(tzinfo=datetime.timezone.utc)
    cur.close()
    conn.close()

    milestone = "final"
    project_name = f"{direction}-{milestone}"
    dataset_name = DATASET_NAMES[direction]
    print(f"[backfill] {project_name}: {len(rows)} rows from live Postgres, "
          f"batch end_time={end_time.isoformat()} (EvalRun.completedAt)")
    if not dry_run:
        ensure_project(client, project_name, dataset_name,
                        description="TRUE FINAL reconciled state -- canonical. Read live from "
                                     "Postgres EvalRun/EvalResult, merging both fix rounds and the "
                                     "never-retested-but-legitimate regression-checked buckets.")
    for row in rows:
        backfill_result_row(client, project_name, dataset_name, direction, milestone, row, end_time, dry_run)
    print(f"[backfill] {project_name}: {'(dry run) would write' if dry_run else 'wrote'} {len(rows)} runs")


MILESTONE_FILES = {
    "legacy": {
        "outbound": ("outbound_legacy-stress-test-2026-08-12_20260826T142747.json", load_wrapped),
        "inbound": ("inbound_legacy-stress-test-2026-08-12_20260826T142903.json", load_wrapped),
    },
    "prefix": {
        "outbound": ("outbound_final_rerun.json", load_plain_list),
        "inbound": ("inbound_final_rerun.json", load_plain_list),
    },
    "round2": {
        "outbound": ("outbound_verify_rerun.json", load_plain_list),
        "inbound": ("inbound_verify_rerun.json", load_plain_list),
    },
    "weakened": {
        "outbound": ("outbound_v2-weakened-no-eta_20260819T151544.json", load_wrapped),
    },
    "flawed": {
        "outbound": ("FINAL_v1-baseline_compiled_20260821.json", load_wrapped),
        "inbound": ("FINAL_v1-baseline_compiled_20260821.json", load_wrapped),
    },
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--milestone", choices=["all", "legacy", "prefix", "round2", "weakened", "flawed", "final"],
                         default="all")
    parser.add_argument("--direction", choices=["outbound", "inbound", "both"], default="both")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    client = get_client()
    directions = ["outbound", "inbound"] if args.direction == "both" else [args.direction]
    milestones = (["legacy", "prefix", "round2", "weakened", "flawed", "final"]
                  if args.milestone == "all" else [args.milestone])

    for milestone in milestones:
        if milestone == "final":
            for direction in directions:
                backfill_final_from_postgres(client, direction, args.dry_run)
            continue
        for direction in directions:
            entry = MILESTONE_FILES[milestone].get(direction)
            if entry is None:
                print(f"[backfill] {direction}-{milestone}: no source file for this direction, skipping")
                continue
            filename, loader = entry
            path = RESULTS / filename
            if not path.exists():
                print(f"[backfill] MISSING FILE, skipping: {path}")
                continue
            backfill_from_file(client, milestone, direction, path, loader, args.dry_run)


if __name__ == "__main__":
    main()
