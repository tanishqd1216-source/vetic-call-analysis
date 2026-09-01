# Extract Shared Runtime Module from agent_outbound.py / agent_inbound.py

Status: **planned, not yet executed** — saved for a future session to pick up.

## Context

`agent_outbound.py` (913 lines) and `agent_inbound.py` (961 lines) are near-duplicate files with no shared module between them. This already caused a real production bug: the NO-ETA safety rule went missing from one file for a period because shared logic is hand-edited in lockstep across two files. It's also the blocker for eventually making agent behavior config-driven (needed to add voice agents beyond inbound/outbound) — you can't generalize a config-driven system from two files that don't even share a common runtime today.

A full line-by-line diff (done during planning) precisely separated what's genuinely identical (safe to extract, zero behavior change) from what only looks similar but has real behavioral differences (must stay file-specific). This plan extracts exactly the first category into a new sibling file, `agent_shared.py`, following the existing flat-file import convention already used by `vetic_live_db.py`/`event_reporter.py` (no `pyproject.toml`/`src/` layout exists or should be introduced).

**Guiding constraint, per the project's own stated priority order ("correctness → cost → latency → maintainability → polish"): this must be a zero-behavior-change refactor.** Every extraction below is scoped to what the diff confirmed byte-identical (or differing by exactly one string-literal parameter). Nothing with genuine content differences moves.

## What moves to `agent_shared.py` (confirmed identical or single-parameter-different)

| Extracted | Source (byte-identical unless noted) | New signature |
|---|---|---|
| `env_bool`, `env_num` | `agent_outbound.py:53-68` / `agent_inbound.py:63-78` | unchanged |
| `setup_langsmith()` | `agent_outbound.py:70-77` / `agent_inbound.py:140-147` — fully identical | unchanged |
| `write_latency_row(row, csv_path, header)` | `agent_outbound.py:90-112` / `agent_inbound.py:159-179` — identical body; only the CSV path was previously a module global | takes `csv_path`/`header` as params instead of reading module-level constants |
| `make_metrics_handler(call_id, call_type, csv_path, header)` | `agent_outbound.py:115-184` / `agent_inbound.py:182-249` — identical except the hardcoded `"outbound"`/`"inbound"` string passed to `fire_event` | `call_type` becomes an explicit parameter |
| `search_knowledge_base_impl(collection, query)` | `agent_outbound.py:562-578` / `agent_inbound.py:689-705` — fully identical body | each file's own `@function_tool` method becomes a thin wrapper keeping its original docstring (the LLM-facing tool description must not change), calling this impl |
| `delete_livekit_room(room_name)` | the 5-line room-deletion block inside `end_call()` in both files — fully identical | new small async helper |
| `build_llm()` | `agent_outbound.py:523-527` / `agent_inbound.py:551-555` — fully identical | unchanged |
| `build_tts()` | `agent_outbound.py:528-540` / `agent_inbound.py:556-568` — fully identical | unchanged |
| `build_vad()` | VAD config in both `__init__`s — fully identical | unchanged |
| `build_stt(prompt)` | STT config in both `__init__`s — identical except the `prompt=` string (inbound's adds phone-number vocabulary) | `prompt` becomes an explicit parameter, each file passes its own (unchanged) prompt text |
| A handful of named prompt-rule constants (see below) | individually confirmed byte-identical sub-rules within `BASE_RULES` | plain string constants |

**Prompt-rule constants** (the piece that directly addresses the original NO-ETA bug): `PERSONA_OPENING_LINE`, `HARD_RULES_INTRO`, `TONE_PARAGRAPH`, `SECURITY_RULE`, `ANTI_GASLIGHTING_RULE`, `EMPATHY_RULE`, `CHURN_THREAT_RULE`, `PREMATURE_END_RULE`, `INTERRUPTION_HANDLING_RULE`, `NO_ETA_RULE` — each confirmed byte-identical text between the two files today. Each agent file's own `BASE_RULES` string is reassembled by interpolating these shared constants alongside its own file-specific rules (outbound's VERIFY/TOPIC MISMATCH/UNKNOWN INFORMATION rules; inbound's IDENTITY-FIRST/EMERGENCY OVERRIDE/STT-robustness/CALL-DROP RECOVERY/DASHBOARD REPORTING rules), preserving exact ordering and surrounding text from each file today.

## What stays file-specific (confirmed genuine differences — do NOT touch)

- **`BASE_RULES`'s non-shared rules** and **all of `CALL_FLOW`** — different resolution-table content, different wording, outbound has an "AT HOME SERVICE" category inbound lacks.
- **`build_instructions()`** — outbound performs `.replace()` templating (pet name, appointment type, team name) against its own `BASE_RULES`/`CALL_FLOW`; inbound does none of this. Fundamentally different functions, not a signature variance.
- **`end_call()`'s message text and event-firing logic** — different goodbye wording (outbound implies a callback since it initiated the call; inbound doesn't), different event payload shape/timing (inbound fires with a rich dashboard-state payload before speaking; outbound fires a simple payload after). Only the room-deletion sub-block factors out (above).
- **`__init__` signature** (`owner_name` param exists only in outbound), **turn-detection/endpointing tuning** (`unlikely_threshold=0.40`/`max_delay=1.5` for outbound vs. framework defaults/`max_delay=2.5` for inbound — a real, live tuning difference), **STT `prompt=` text content** itself (only the *parameter mechanism* is shared, per `build_stt` above).
- **`entrypoint()`'s call-setup section** — outbound resolves a customer record and personalizes the greeting; inbound uses a fixed generic greeting since it doesn't know the caller yet.
- **All inbound-only functionality**: `_resolve_caller_phone_number`, `_CONTEXT_FIELD_MAP`, `_parse_context_fields`, `_write_dashboard_state`, `_lookup_and_apply`, and the three inbound-only `@function_tool`s (`lookup_customer`, `use_caller_id`, `report_dashboard_state`) — no outbound counterpart exists or should be added.

## Verification (this is what actually proves zero behavior change)

1. **`python -m py_compile agent_shared.py agent_outbound.py agent_inbound.py`** — basic sanity check both files still parse.
2. **Byte-for-byte BASE_RULES check**: before editing, capture each file's current `BASE_RULES` string (e.g. `git show HEAD:agent_outbound.py` piped through a small script that imports/evals just the constant, or a quick `python -c` diff). After editing, recompute the reassembled `BASE_RULES` in each file and diff against the captured baseline — must be byte-identical. This is the single highest-risk part of this refactor (prompt rules are exactly what an LLM's behavior is sensitive to), so it gets a dedicated, explicit pass rather than being folded into a general test run.
3. **Re-run `tests/run_stress_test_inbound.py`** (the one currently-working stress test — `tests/run_stress_test.py` has a pre-existing broken import unrelated to this refactor) and compare its pass rate against the baseline already recorded in `tests/results/*inbound*.md`.
4. **One real end-to-end call each direction**, using the same method that already worked for the M1/M4 dashboard-integration verification: start `webcall_server.py` + `agent_outbound.py dev`, place a real browser test call, then confirm via `GET /api/calls/:callId` against the Meridian dashboard backend that `CallTurn` rows and `call_started`/`call_ended` `CallEvent` payloads look the same shape as the pre-refactor historical calls already verified (`web-call-efe30c86` for outbound, `web-call-fe050e1f` for inbound) — same field names, same value ranges, latency CSV rows still written to the correct per-direction path. Repeat for `agent_inbound.py dev`. This is the strongest check since it exercises every extracted piece at once (metrics handler, latency writer, knowledge-base search, room deletion on hangup, LLM/TTS/VAD construction, LangSmith setup) under real conditions.
5. Review the full `git diff` for both agent files before considering this done — every hunk should be either "removed, now imported from `agent_shared`" or "identical text, just reassembled from constants," nothing else.

## Explicitly out of scope for this pass

The pre-existing dead `import re as _re` in `agent_outbound.py`'s `build_instructions` region (never used) and the broken `tests/run_stress_test.py` import are both unrelated pre-existing issues, left untouched unless included deliberately later.
