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
