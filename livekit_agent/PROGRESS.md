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
