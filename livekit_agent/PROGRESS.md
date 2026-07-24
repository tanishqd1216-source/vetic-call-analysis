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

## 2026-07-24 — Session: git cleanup + Fly.io worker hosting

**Decisions from user this session:**
- Committed the reorg from the previous session, split into two commits: (1) untracking a
  stale `venv/`/`twilio_test/venv/` still tracked from old history (already moved to
  `archive/stale_venv/` and `archive/legacy_twilio/venv/` on disk, now gitignored so they
  don't come back), (2) the actual reorg + code changes (`agent_inbound.py`, tests/tools/logs
  restructuring, knowledge base updates).
- `origin` pointed at a GitHub repo (`tanishqd1216-source/vetic-call-analysis`) that no longer
  existed — recreated it (private) and pushed `main`. Note: full history is ~425MB (old
  venv/media commits pre-cleanup); GitHub warned about a few >50MB files baked into old commits
  (`cloudflared.exe`, an ffmpeg binary, a native `.pyd`) — not blocking, but worth a history
  rewrite/squash later if repo size becomes a problem.
- Mission priority chosen: **production deployment** of the worker process, scoped to
  **hosting/reliability only** — no telephony (SIP trunk/real phone number) yet, that's an
  explicit later phase. Hosting target: **Fly.io, Mumbai (`bom`) region**, single always-on
  machine (no scale-to-zero).
- Both agent variants (`agent.py`, `agent_inbound.py`) stay deployable from one image,
  selectable via `AGENT_ENTRYPOINT` env var, since neither is being retired.

**Shipped this session:**
- Fixed hardcoded Windows-absolute-path defaults that would break on a Linux container:
  `vetic_db.py` (`DB_PATH`), `agent.py` (`LATENCY_CSV_PATH`), `agent_inbound.py`
  (`LATENCY_CSV_PATH`) — all now default relative to the file's own directory, still
  overridable via env var.
- Copied the sample customer DB into `data/vetic_data.db` so it ships with the image (this is
  test/sample data, not a live connection to Vetic's real Postgres/Mongo — see gap below).
- Added `run.py` — container entrypoint that execs `agent.py` or `agent_inbound.py` in `start`
  (production) mode based on `AGENT_ENTRYPOINT`.
- Added `Dockerfile` + `.dockerignore` (`python:3.12-slim`, bakes in `knowledge/`,
  `knowledge_db/`, `data/`).
- Added `fly.toml` — app `vetic-voice-agent`, region `bom`, `min_machines_running = 1`,
  `auto_stop_machines = false`, health check on port `8081` (confirmed this is
  `livekit-agents` 1.6.5's default `WorkerOptions.port` in `start` mode — no code change
  needed for that part).
- Added a structured `latency_metric key=value ...` log line alongside the existing CSV
  latency logging in both agents, so per-turn latency/tokens are greppable straight out of
  `fly logs` (the CSV itself lives on ephemeral container disk and won't survive redeploys).
- Rewrote `README.md`'s architecture section (was still describing Claude 3.5 — actual LLM is
  `gpt-4o-mini` + optional Headroom proxy) and added a "Production Deployment" section
  documenting the `fly deploy` flow, secrets setup, and how to switch `AGENT_ENTRYPOINT`.

**Known gaps / explicitly deferred (not done this session):**
- `data/vetic_data.db` is still a static sample file, not live Vetic Postgres/Mongo data —
  needs to change before this handles real customers.
- No telephony/SIP trunk — deployment only proves the worker process itself is reliable.
- No Docker or Fly CLI installed locally — deploy steps are documented but not yet executed;
  the actual `fly deploy` needs to be run by the user (or a follow-up session with those tools
  available).
- Headroom proxy / prompt-caching cost work still not verified as actually reducing billed
  tokens — deferred until there's a real deployed baseline to benchmark against.
- Grafana/Loki integration for the new `latency_metric` log lines not wired up — Vetic already
  has both, flagged as a fast-follow once there's a concrete log-shipping path.

**Next steps:**
1. Run `fly deploy` (or `--remote-only`) and verify per the README's "Checking it's alive"
   section.
2. Once deployed and confirmed healthy, re-run `tests/run_stress_test.py` /
   `run_stress_test_inbound.py` against it to confirm no regression from the path-default
   changes.
3. Then return to the cost/latency thread: verify Headroom/prompt-caching effectiveness against
   real deployed numbers, not local dev ones.
