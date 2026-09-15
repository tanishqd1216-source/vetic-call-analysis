# Eval reconciliation investigation (archived, 2026-09)

These scripts were the working set used to investigate and partially fix a real eval-integrity
bug: a reconciliation report that claimed "39 misroutes fixed, ~100% passing" turned out, on a
live rerun against the unmodified agent, to have only 20 of those 39 actually passing — the rest
were relabeled old transcripts, not fresh agent behavior. See `CASE_STUDY.md`'s "The eval harness
and dashboard" section for the full story, including the two real fix-and-verify rounds that
followed.

Superseded by the LangSmith-based harness (`../../livekit_agent/tests/eval/run_langsmith_eval.py`,
`langsmith_setup_datasets.py`, `langsmith_backfill.py`) — kept here for the record rather than
deleted, matching this repo's existing `archive/legacy_twilio/` convention, since the investigation
trail is itself part of what this project demonstrates.

`verify_inbound_35_check.py`, `verify_inbound_10_new_check.py`, `verify_unaddressed_fixes.py`, and
`verify_routing_fixes.py` are NOT here — they're a real transitive dependency chain
(`compile_inbound_100.py` imports the first two directly; `verify_inbound_35_check.py` imports the
third; that imports the fourth) still needed by `langsmith_setup_datasets.py`'s expected-team data,
and remain in `livekit_agent/tests/eval/`. Confirmed by walking the actual import graph, not
assumed.

Not guaranteed to still run as-is (they import from modules whose contents have since evolved) —
this is a historical snapshot, not maintained code.
