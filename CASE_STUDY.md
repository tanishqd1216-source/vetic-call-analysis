# Vetic Calling Agent — an outbound service-recovery voice agent

*An AI PM case study, written from the builder's seat.*

## The one-liner

An AI voice agent that calls pet owners after a bad grooming experience, walks them through a structured recovery conversation, and hands off a logged, escalated complaint — built as a Twilio + Claude MVP, tested with real phone calls, then rebuilt on LiveKit + OpenAI after the MVP's architecture hit a real latency ceiling.

## The problem

Vetic runs pet grooming appointments, and like any service business, some of them go wrong. Today, following up on an unhappy customer is a human agent's job: call the customer, absorb the complaint, apologize where warranted, log it, and promise a resolution. It's high-value work — a bad follow-up call turns one bad appointment into a lost customer — but it's also repetitive enough to automate, and there was already a body of real transcripts (`twilio_test/Call Analysis Model - Analysis ( ms, ra, t).csv`) showing what a *good* human version of this call looks like, graded against an internal SOP. That transcript set became the source material the agent's prompts were distilled from.

So the brief wasn't "build a chatbot that talks." It was: can an AI agent run the empathetic, structured part of a service-recovery call — reliably, at phone-call latency, without inventing promises the business can't keep — well enough to either handle these calls directly or triage them before a human takes over?

## v1: the Twilio + Claude MVP

The first build (`twilio_test/`) is a FastAPI app sitting behind Twilio's voice webhooks — `/incoming_call`, `/process_speech`, `/call_status` — using Twilio's own `<Gather>` for turn-taking rather than a raw bidirectional media stream. Each turn: Twilio transcribes the caller's speech, posts it to the server, the server builds a stage-specific prompt, calls Claude, strips control tags out of the response, and plays back Sarvam-generated TTS audio.

The conversation itself is a **4-stage, tag-driven state machine**, keyed by Twilio's `CallSid`:

1. **Introduction** — confirm identity, confirm it's a good time to talk, ask about the pet by name, then transition.
2. **Context** — ask exactly one question at a time, capture the complaint under a strict "no assumption" rule (repeat it back before moving on), explicitly defer any talk of resolution.
3. **Resolution** — apologize *exactly once, and only if Vetic was at fault* ("never follow an apology with a defensive excuse, or the word 'but'"), convert empathy into itemized actions — log the complaint, raise an escalation ticket, promise a callback ETA — then close with a fixed line and hand off.
4. **Conclusion** — reassure without inventing new promises, thank them, close.

Each stage exits by emitting `[NEXT_STAGE]` or `[END_CALL]`, which the server regex-strips and uses to drive the state transition. That's a deliberately narrow design: the model doesn't decide the conversation structure, it fills in language within a structure a human already validated against real SOP-graded calls.

Real calls were placed and recorded against this build — recordings and generated TTS clips exist as evidence, not just a design doc. That matters, because it's the difference between "I designed a voice agent" and "I shipped one and heard what it sounded like on a real phone call."

## Defining "a good call"

This is the part I'd flag first in an interview, because it's honest rather than tidy: **what got measured and what didn't measure are two different things, and the gap between them is the most important finding in this project.**

**What's actually measured**, in the rebuilt `livekit_agent/`:

- A **policy-compliance rubric**, judged by an LLM (`tests/eval/rubric.py` + `run_eval.py`, judge model `gpt-5`), scored against explicit business-rule tags: `ZERO_REFUND`, `NO_ETA`, `NO_REPETITION`, `EMERGENCY_LOCATION_FALLBACK`, `IDENTITY_VERIFICATION`. This checks *does the agent follow the rules it was given* — did it avoid promising a refund it can't authorize, did it verify identity, did it avoid repeating itself.
- A **stress-test suite** (`run_stress_test.py`) that grades identity-resolution against a held-out set — scored at 90/100 first-try matches.

**What's not measured, anywhere in either build:**

- **Sentiment.** No sentiment scoring exists on either side of this project. "Was the customer calmer at the end of the call than the start?" — the single most PM-relevant signal for a *recovery* call — is currently invisible.
- **Completion rate.** There's no tracked metric for what fraction of calls make it cleanly through all four stages versus drop, stall, or dead-end.
- **True task success.** The rubric checks whether the agent *says* the right things — it doesn't check whether the right things actually *happen*. Concretely: Stage 3 promises a callback "within 2-3 hours," but nothing in the codebase schedules that callback. The agent can pass every rubric check on a call where the promised follow-up never occurs. "Policy-compliant" and "actually resolved" are being conflated, and right now only the first one is instrumented.

I'd treat closing that gap — turning "did the agent follow the script" into "did the customer's problem actually get fixed" — as the single highest-leverage next step on this project.

## Tradeoffs

**Latency vs. flexibility.** The MVP uses Twilio's native speech recognizer instead of Sarvam's STT, purely to cut a network round trip out of the per-turn loop — Sarvam is used for TTS only. There's also an intent fast-path that skips the LLM and TTS entirely when the caller's utterance obviously signals the call is over, and the RAG layer (`knowledge/rag.py`) is a deliberately zero-embedding, scenario-and-stage keyed few-shot lookup rather than a real retrieval system — a conscious accuracy-for-speed trade justified by the fact that this is a phone call, where "perceived lag is what makes the agent feel robotic" (the project's own words, from `latency_optimization.md`). This wasn't guesswork: `latency_logs.csv` has 67 rows of real measured per-turn latency (end-of-utterance delay, LLM time-to-first-token, TTS time-to-first-byte) from actual test calls.

**Cost vs. capability — including a mistake worth owning.** The code has a `ROUTINE_MODEL` / `COMPLEX_MODEL` split that was clearly meant to route simple turns to a cheaper model and harder ones to a stronger one. In the current codebase both constants point at the same model (`claude-haiku-4-5`), so the routing logic is a silent no-op — a real bug, not a design choice, and one I'd rather name than hide, because catching it is exactly the kind of thing a PM should be checking for in an agent's cost structure. The rebuild handled cost more rigorously: it empirically measured prompt-caching hit rate (~99%) as the actual cost lever, and evaluated a cheaper OpenAI model (`gpt-5.4-nano`) against `gpt-4o-mini` on real cost/latency data before deciding it had no upside worth chasing. That's the right process — evaluate on evidence — even where the earlier build didn't apply it.

**Build vs. rebuild.** The Twilio `<Gather>` webhook loop was the right MVP choice: it shipped fast and proved the conversation design against real calls. But it's fundamentally a request/response loop dressed up as a phone call, not a real-time bidirectional stream — the project's own `latency_optimization.md` scoped a Media-Streams-class rewrite as "the big win, the big rework" before it happened. That rewrite arrived as a full migration to LiveKit + OpenAI, which is architecturally the real-time system the MVP was always going to need. That's the tradeoff worth surfacing in an interview: ship the simpler architecture first, let it tell you exactly where it breaks, then rebuild with evidence instead of guessing up front.

## What I'd change with more time

In rough priority order:

1. **Close the metrics gap.** Add sentiment scoring and real task-success tracking (did the promised callback actually happen?) instead of relying solely on rubric compliance.
2. **Fix the dead model routing** so the cost/capability split actually does what it was built to do.
3. **Make the callback real.** Right now the agent's biggest promise to the customer is unbacked by any system — that's a trust risk, not just a metrics gap.
4. **Fix the security issues found along the way**: API credentials were stored in plaintext in `.env`, and a config endpoint (`GET /api/config` in `webcall_server.py`) was leaking raw API secrets to the client. Both are fixable in an afternoon; both are the kind of thing that should be caught before, not after.
5. **Add call-hours/DND enforcement and retry logic** — neither build has any concept of "don't call before 9am" or "retry a dropped call once," both of which are table stakes for outbound calling at any real volume.
6. **Replace the Excel-file "database"** — the current data source is an Excel file loaded from a hardcoded path on one developer's machine, and the phone-matching column the code expects isn't even present in the sample data. Fine for a prototype; not fine for anything past it.

## Evidence

Everything above traces back to a real, git-tracked repo at `D:\vetic-call-analysis-main` (8 commits, 2026-06-23 → 2026-07-01, continued in `livekit_agent/PROGRESS.md` through 2026-07-31). Specific files worth opening for follow-up:

- `twilio_test/server.py` — the call/state orchestration and the model-routing bug
- `twilio_test/prompts/gemini/{1_introduction,2_context,3_resolution,4_conclusion}.txt` — the 4-stage script
- `twilio_test/knowledge/rag.py` — the zero-embedding few-shot layer
- `twilio_test/latency_optimization.md`, `twilio_test/latency_logs.csv` — the latency work and its data
- `twilio_test/CALL_QUALITY.md` — the project's own list of known open issues
- `livekit_agent/tests/eval/rubric.py`, `run_eval.py` — the LLM-judge policy-compliance harness
- `livekit_agent/tests/run_stress_test.py` — the identity-resolution stress test
- `livekit_agent/PROGRESS.md` — the rebuild's cost/latency investigation and real end-to-end test log
