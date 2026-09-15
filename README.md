# Vetic Calling Agent

An AI voice agent that runs the customer-recovery call for a pet-grooming business: outbound
service-recovery calls after a bad appointment, and inbound support calls. Built as a Twilio +
Claude MVP, rebuilt on LiveKit + OpenAI once the MVP hit a measured latency ceiling, backed by an
eval harness that ended up being the more interesting half of the project.

**Start here:** [`CASE_STUDY.md`](CASE_STUDY.md) — the real story, written from the builder's
seat: the MVP → rebuild arc, the model cost/latency tradeoff, and the eval-integrity investigation
that found a "100% passing" report was actually hiding real failures — plus what it took to fix
that for real (two live fix-and-verify rounds, real regressions found and fixed, a documented
compliance-type gap that two independent fix attempts couldn't close).

## Repo layout

- **[`livekit_agent/`](livekit_agent/README.md)** — the agent itself. Two entrypoints
  (`agent_outbound.py`, `agent_inbound.py`) sharing one LiveKit/Sarvam/OpenAI stack; see that
  README for the full architecture, prompt design, business rules, and setup instructions.
- **`livekit_agent/tests/eval/`** — the eval harness. `rubric.py` + `run_eval.py` grade simulated
  calls against tagged business rules using an independent LLM judge; `run_langsmith_eval.py` is
  the current go-forward runner, tracked in [LangSmith](https://smith.langchain.com) (two reference
  datasets, real historical experiments from legacy grading through the eval-integrity fix). Real,
  live-verified numbers as of the last full rerun: **71/100 outbound, 72/100 inbound** on
  classification; **95% outbound / ~4% inbound** on an empathy-acknowledgment check — the inbound
  gap is real, diagnosed, and still open (see `CASE_STUDY.md`).
- **`archive/eval_reconciliation_investigation/`** — the earlier, bespoke eval-reconciliation
  scripts that found the original eval-integrity bug, kept for the record rather than deleted once
  the harness moved to LangSmith.

## Related

- **[Meridian dashboard](https://github.com/tanishqd1216-source/ai-call-agent-master)** — the
  separate Postgres/Next.js app for call-history observability, an ERP-style department/agent
  picker, and CRM/MCP integration management. It used to also host eval tracking (`/evals`); that's
  been decommissioned in favor of LangSmith, see its own README for what it does now.
