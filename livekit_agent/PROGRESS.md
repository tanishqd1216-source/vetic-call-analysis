# Vetic Voice Agent — Mission Progress Log

Standing mission: build and continuously operate a production-grade AI agent, optimized for
cost efficiency and low latency. Priority order: correctness → cost → latency → maintainability
→ polish. Never hard-delete files — move to `archive/` instead. Log state here at the end of
every work block so a new session can resume without re-discovering everything.

---

## 2026-07-24 — Session: initial assessment (post-/clear)

**State found (not created this session):**
- Two working agents: `agent.py` (outbound) and `agent_inbound.py` (inbound, identity-first).
  Both use LiveKit + Sarvam STT/TTS + **gpt-4o-mini** (OpenAI), optionally routed through a local
  **Headroom proxy** (`headroom-ai` pip package, `127.0.0.1:8787`) that compresses the outgoing
  prompt to cut token cost. `README.md` is stale — still describes a Claude-based pipeline.
- Both agents pass a shared 100-query support-taxonomy stress test with high correctness
  (inbound: 90/100 first-try identity matches; most category/resolution mappings correct).
  Results in `tests/results/stress_test_results_full.md` / `_inbound.md`.
- Outbound latency log (`logs/latency_logs_outbound.csv`): ~1.2–2.7s typical response latency,
  ~5000 input tokens/turn — the full system prompt + resolution lookup table is resent every
  turn. **This is the clearest cost/latency lever available**: prompt caching (OpenAI's automatic
  prompt caching kicks in >1024 tokens with zero code changes, or the Headroom proxy) should cut
  this significantly if not already effective.
- Inbound latency log rows show 0 input/output tokens and suspiciously low latency (~0.3–0.4s) —
  looks like a metrics-capture gap for the inbound flow, not a real perf win. **Needs
  verification before trusting inbound numbers.**
- No production deployment exists (no Dockerfile/compose/process manager/monitoring). Everything
  runs via local dev commands + ngrok/cloudflared tunnels.
- Working tree has a large uncommitted reorg already done on disk (files moved into `archive/`,
  `tools/`, `tests/`, `logs/`) but not yet `git add`ed as moves — git still shows deletes. Not
  touched this session; flagged to user before committing.

**Actions taken this session:** read-only assessment only (agent.py, agent_inbound.py, README,
requirements.txt, test results, latency logs, dashboard_state.json). Wrote persistent memory
entries for project architecture + the "never hard-delete" rule. Created this file.

**Next steps (proposed, awaiting user direction on priority):**
1. Fix the inbound metrics-capture gap so latency numbers are trustworthy before optimizing on
   top of them.
2. Verify whether Headroom proxy / OpenAI automatic prompt caching is actually reducing billed
   tokens for the ~5000-token/turn system prompt (check `/stats` on the proxy while it's running,
   or OpenAI usage dashboard) — if not, wire it in properly.
3. Decide: is production deployment in scope now, or is the near-term focus more cost/latency
   tuning of the local setup? (asked user — see conversation)
4. Update `README.md` to reflect the actual gpt-4o-mini + Headroom architecture.
5. Once the large on-disk reorg is confirmed intentional, stage it as a commit (currently
   uncommitted).

---

## 2026-07-24 — Session: git cleanup landed on main; Fly.io hosting explored then shelved

**Git cleanup (this landed on `main`, see commits `0aa539b9` / `843cb73d`):** the on-disk reorg
from the previous session was committed as two separate commits — untracking a stale
`venv/`/`twilio_test/venv/` (already moved to `archive/stale_venv/` and
`archive/legacy_twilio/venv/` on disk, now gitignored), then the actual reorg + code changes
(`agent_inbound.py`, tests/tools/logs restructuring, knowledge base updates). `origin` pointed at
a GitHub repo that no longer existed — recreated (`tanishqd1216-source/vetic-call-analysis`,
private) and pushed. Full history is ~425MB (old venv/media commits pre-cleanup) — GitHub warned
about a few >50MB files baked into old commits; not blocking, worth a history squash later.

**Fly.io hosting — built, NOT merged, decision reversed on cost grounds:** production hosting
was picked as the mission priority, scoped to worker reliability only (no telephony yet). Built
out a full Fly.io deployment (portable path-default fixes, `data/vetic_data.db` bundling,
mode-selectable `run.py` entrypoint, `Dockerfile`/`.dockerignore`/`fly.toml` for `bom` region,
structured `latency_metric` log lines, README production-deployment section) on branch
**`deploy/fly-io-worker-hosting`** (pushed to origin, PR link available, NOT merged into main).

Got as far as installing `flyctl` locally (blocked by this machine's Application Control policy
at first — resolved by the user allowing it — then authenticated via `flyctl auth login`) before
discovering Fly.io no longer has a real free tier: any always-on machine is billed usage-based.
**User wants $0 hosting cost**, which Fly.io can't satisfy for an always-on process. Decision:
**stay on local hosting for now** (same as before — `start_call_environment.ps1` on a dev
machine) rather than pay for Fly, and rather than compromise on "always-on" by using a
scale-to-zero free host (bad fit for a voice agent that needs instant call dispatch).

Real zero-cost always-on alternative surfaced but not pursued yet: **Oracle Cloud's free-forever
tier** (small ARM VM, always-on, genuinely $0) — requires a credit card for identity verification
(never charged if within free-tier limits) and the free VM is ARM, so the existing Dockerfile
would need a base-image tweak. Worth revisiting if $0 hosting is wanted again later; the branch's
Docker/fly.toml work is largely reusable (swap `fly.toml` for an Oracle VM + systemd unit, or
adapt the Dockerfile for ARM).

**Current reality:** `main` does NOT have the path-default fixes or Dockerfile — those only
exist on the unmerged `deploy/fly-io-worker-hosting` branch. The agent still only runs via local
dev commands on whichever machine has `start_call_environment.ps1` running.

**Next steps:**
1. Since hosting is deferred, the next highest-leverage mission item (per priority order
   correctness → cost → latency) is the still-unverified **Headroom proxy / prompt-caching cost
   question** from the first session entry above — pick that back up.
2. If $0 always-on hosting becomes wanted again, revisit Oracle Cloud free tier using
   `deploy/fly-io-worker-hosting` as a starting point (adjust `fly.toml`→Oracle equivalent, ARM
   base image).

---

## 2026-07-24 — Session: verified prompt caching is already working; found a model question

**Verified (empirically, via 2 real `gpt-4o-mini` API calls using the actual `build_instructions()`
system prompt against the live OpenAI API):** OpenAI's automatic prompt caching **is already
active with zero code changes** — turn 1 of a simulated call showed `cached_tokens=0` (expected,
first time seeing that exact prefix), turn 2 (same system prompt + one more turn appended, as
happens in every real call) showed `cached_tokens=4864` out of `4907` prompt tokens — **~99% of
the input was a cache hit**. This resolves the open question from the first session entry:
Headroom's 0% compression (already documented, disabled in `.env`) doesn't matter here because
OpenAI's native caching already handles the static ~5000-token system prompt for free, turn 2
onward within a call. No further action needed on this thread — it was already solved, just
unverified.

**New finding, not yet acted on:** `gpt-4o-mini` no longer appears on OpenAI's current pricing
page (`developers.openai.com/api/docs/pricing`) — checked the deprecations page too, it's **not
officially deprecated**, just absent from the current flagship pricing table (OpenAI's lineup has
moved to a gpt-5.x family since this was originally wired up). Current cheapest actively-listed
tier is **`gpt-5.4-nano`** at $0.20/1M input / $1.25/1M output (vs. legacy `gpt-4o-mini` pricing
that isn't independently confirmable from the current page). Notably `gpt-5.4-nano`'s *output*
price is over 2x `gpt-4o-mini`'s historical output price — so this is NOT a clear cost win, and
swapping the core LLM is a correctness-affecting change (priority #1 in the mission order) that
needs validation against the existing 100-query stress-test suite before switching, not a
speculative swap. Flagged to user; awaiting direction before touching the model.

**Next steps:**
1. If asked to proceed: run `tests/run_stress_test.py` / `run_stress_test_inbound.py` against
   `gpt-5.4-nano` alongside the existing `gpt-4o-mini` results, compare correctness AND actual
   per-call cost/latency before recommending a switch.
2. Otherwise, cost/latency thread is now in a good state (caching confirmed working) — next
   mission focus is open.

---

## 2026-07-24 — Session: gpt-5.4-nano evaluated on the stress suite — rejected, staying on gpt-4o-mini

Ran the 100-query outbound stress suite (`tests/run_stress_test.py`, now parametrized via
`STRESS_TEST_MODEL` env var — simulated customer stays fixed on `gpt-4o-mini` so only the
agent's model varies between runs) against both models for a controlled comparison:

- **`gpt-4o-mini` (re-run on the same instrumented harness, results:
  `tests/results/stress_test_results_full_gpt-4o-mini.md`):** completed the full 100-query sweep
  in ~14 minutes. Agent-side usage: 5,455,196 prompt tokens, **5,277,312 (96.7%) cache hits**,
  14,989 completion tokens.
- **`gpt-5.4-nano`:** a single-query smoke test worked correctly (sensible resolution, tool-call-free
  chat completions, cache hits present too — 55,296/68,837 prompt tokens cached within that one
  call) but required `max_completion_tokens` instead of `max_tokens` (handled via a fallback helper
  in the test script). The full 100-query run was **killed after running far longer than the
  gpt-4o-mini run without finishing** — noticeably higher per-call latency was already visible in
  the single-query smoke test, and held up across the full run. **Decision: keep `gpt-4o-mini`.**
  Given nano's higher output-token price (see prior entry) and now confirmed-worse latency, there
  was no upside left to chase.

**Outcome:** the model stays `gpt-4o-mini`. The cost/latency thread that opened this investigation
(is anything cutting the ~5000-token/turn cost) is now fully closed: prompt caching handles it
automatically, already confirmed working, no cheaper/faster alternative model found. Mission focus
is open again — next highest-leverage item to be picked at the next session/request.

**Housekeeping:** `tests/run_stress_test.py`'s model-parametrization change is not yet committed.
`tests/results/stress_test_results_full_gpt-4o-mini.md` is a new file alongside the original
`stress_test_results_full.md` (same content in spirit, regenerated with token-usage tracking) —
minor duplication, not cleaned up.

---

## 2026-07-25 — Session: verified inbound latency metrics; cleaned up stale Claude-era docs

**Verified the inbound metrics-capture gap flagged since the first session was never a real bug.**
Cross-checked the historical worker log `logs/agent_inbound_run.log` (a real inbound test run,
2026-07-24 13:13–13:17) and confirmed `EOUMetrics`/`LLMMetrics`/`TTSMetrics` correlate correctly by
`speech_id` and produce real non-zero values across ~13 turns (e.g. one turn: EOU 3.18s, LLM 3.13s,
6377 input / 23 output tokens). The `make_metrics_handler` logic in `agent.py` and `agent_inbound.py`
is byte-identical — there's no code-level asymmetry between outbound and inbound. The all-zero rows
sitting in the current `logs/latency_logs_inbound.csv` (3 rows, one short webcall test, Call_ID
`web-call-35afe6a5`) are from a call that ended before any real STT→LLM turn completed — an
artifact of that one short test, not a systemic capture gap. **No code change needed; this item is
closed.** If inbound latency numbers are needed going forward, run a longer inbound test call (or
`tests/run_stress_test_inbound.py`) to get a fresh, trustworthy CSV sample.

**Fixed stale Claude-era docs/comments (mission item flagged repeatedly, never actioned):** found
and removed a dead `from livekit.plugins import ... anthropic` import in both `agent.py` and
`agent_inbound.py` (grepped for `anthropic.` usage — zero hits, fully dead since the Claude→OpenAI
migration). Updated module docstrings, `README.md` (LLM section, env var table, added the
previously-undocumented `agent_inbound.py` entrypoint and its run command, noted the Headroom
proxy + confirmed-working OpenAI prompt caching), `.env.example` (`ANTHROPIC_API_KEY` →
`OPENAI_API_KEY` + optional `HEADROOM_PROXY_URL`, removed the dead `LLM_MODEL` var — the model is
hardcoded `gpt-4o-mini` in code, no env override exists), and `requirements.txt`
(`livekit-agents[anthropic]` → `livekit-agents[openai]`, matching what's actually imported and
installed in `.venv`). This closes the "Known stale/messy state" README item from project memory.

**Also committed this session:** the previously-uncommitted `tests/run_stress_test.py`
model-parametrization change and the new `tests/results/stress_test_results_full_gpt-4o-mini.md`
(from the gpt-5.4-nano vs gpt-4o-mini comparison two sessions ago) — this was finished work sitting
uncommitted since then.

**Next steps:** mission focus is open again. Candidates not yet done: squash the ~425MB git
history (old venv/media commits pre-cleanup — not urgent), or revisit Oracle Cloud free-tier
hosting if $0 always-on hosting is wanted again (see architecture memory). Otherwise, no open
correctness/cost/latency items remain — next session should scan for a new highest-leverage item
per the standing mission priority order.

---

## 2026-07-25 — Session (cont'd): two real correctness bugs found in inbound results, fixed + verified

Went back into `tests/results/stress_test_results_inbound.md` to root-cause the 4 rows the
summary table flagged as `Not reached`, instead of trusting the "90/100" headline stat. Found:

- **Row 100** (dropped-call edge case) and **Row 24** (a pure FAQ question, correctly answered
  inline with no ticket needed) are false positives of the test harness's keyword-based
  resolution detector — not agent bugs.
- **Row 14 (OPD — Critical Pet) is a real bug:** in a life-threatening-emergency scenario, the
  agent asked "What is your current location?" **six times in a row** and never reached its own
  documented fallback ("only if their location is still unclear after 2 attempts") — the count-based
  trigger relies on `gpt-4o-mini` reliably counting its own prior attempts across turns, which it
  isn't doing. Also found `agent.py` (outbound) had no fallback for this at all.
- **Row 19 (Appointment_Process — Too Many Calls) is a real bug:** after resolving a
  do-not-disturb request, the customer asked "how long will it take?" and the agent replied
  "typically takes a few business days" — directly violating its own NO-ETA RULE, which names
  that exact phrase as forbidden. Investigating further found **`agent.py` (outbound) had no
  NO-ETA RULE in its HARD RULES at all** — it only ever existed in `agent_inbound.py`. Given the
  business rule is core policy (documented in README's "Core Business Logic & Constraints"), this
  was a real gap in the production outbound prompt, not just a one-off model slip.

**Fix approach (per user direction — keep the existing rule structures, make them more explicit
rather than redesigning the logic):**
1. Rewrote the critical-condition emergency RESOLUTION in both `agent.py` and `agent_inbound.py`:
   split the vague "ACTION 4 (FALLBACK — after 2 attempts)" into explicit numbered steps —
   ACTION 2 (first attempt) → ACTION 3 (success path) → ACTION 3b (one explicit retry, only if
   ACTION 2's reply lacked a real place name) → ACTION 4 (mandatory fallback the instant ACTION 3b
   also lacks a real place name, explicitly forbidding a 3rd attempt). Content-anchored ("lacks a
   real place name") rather than pure attempt-counting.
2. Added the missing `NO-ETA RULE` to `agent.py`'s HARD RULES (copied from `agent_inbound.py`,
   now present in both).
3. Added a `CRITICAL NO-ETA REMINDER` inline in both files' shared STRICT 5-STEP resolution
   pattern (right after the no-repetition rule), since that's the point in the flow where a
   "how long will it take" follow-up is most likely to land.

**Verified via a targeted scripted re-run** (not the full 100/200-query suite — 4 scripted,
deterministic-customer-turn conversations against live `gpt-4o-mini`, reusing
`tests/run_stress_test_inbound.py`'s real tool-calling harness pattern): both the emergency-location
fallback and the NO-ETA compliance now behave correctly in **both** `agent.py` and
`agent_inbound.py`. Ad hoc verification script was scratch-only (job tmp dir), not committed.

**Not yet investigated:** whether other categories in the RESOLUTION LOOKUP TABLE have similar
"freeform improvisation, no scripted NO-ETA-safe phrasing" gaps the way the do-not-disturb bullet
did — this fix addresses the 2 specific bugs found, not a systematic audit of every category.

**Next steps:** if continuing this thread, a full 100-query re-run of both suites against the
patched prompts would give a real before/after regression check (skipped this session per user's
choice of a cheaper targeted verification). Otherwise, mission focus is open again — git history
squash and Oracle Cloud hosting are the remaining known candidates.
