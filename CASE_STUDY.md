# Vetic Calling Agent — from a Twilio MVP to a rebuilt agent with its own dashboard

*An AI PM case study, written from the builder's seat.*

## Executive summary

Vetic runs pet grooming appointments; when one goes badly, a human agent calls the owner to apologize, log the complaint, and promise a fix. This project automated that call: a Twilio + Claude MVP, rebuilt on LiveKit + OpenAI after the MVP hit a real, measured latency ceiling, backed by an eval harness (a 30-case golden set, 30/30 pass) and, later, a purpose-built observability dashboard the agent reports into. The standout decision was rejecting a cheaper-looking model swap — `gpt-5.4-nano` had a lower headline input price than `gpt-4o-mini`, but a real, timed 100-query run showed it was slower and, on output tokens, actually more expensive; the incumbent stayed. The most interesting finding, though, wasn't a metric — it was what a "100% passing" eval report actually turns out to mean once you open it up. A later 100-query routing sweep caught 39 real classification failures; roughly two-thirds were genuinely fixed and re-verified against the judge, but the script that assembled the final "reconciled" report hardcoded a pass verdict into every row rather than reading back real results — a meaningful share of the "fixed" cases were never actually re-confirmed. That's not a footnote; it's the same lesson as the sentiment/completion-rate gap below, told twice.

## The problem

Following up with an unhappy customer is high-value, repetitive work: apologize where warranted, log the complaint, promise a resolution — and a bad follow-up call turns one bad appointment into a lost customer. The brief wasn't "build a chatbot that talks." It was whether an AI agent could run the structured, empathetic part of that call reliably, at phone-call latency, without inventing promises the business can't keep. This was a solo build (one author, git history from 2026-06-23 through at least 2026-08-30) under an explicit $0-hosting constraint — a hosting branch for Fly.io was built and then shelved the same day once it became clear Fly.io has no free tier for an always-on worker. The first build, a Twilio + Claude MVP, worked, but its own latency notes named the problem before the fix existed: on real test calls, caller-perceived latency ran to roughly 5.3 seconds per turn, and the project's own `latency_optimization.md` says outright that "perceived lag is what makes the agent feel robotic" — with a from-scratch move to a real-time, WebRTC-style architecture already ranked as the last, biggest lever on the list, before anyone built it.

## Defining success — how I measured a good call

This is the section I'd lead with in an interview, because it's honest rather than tidy: what got measured and what actually mattered turned out to be two different things.

**What's real:** an LLM-judged rubric (12+ tagged business rules — no unauthorized refunds, no missed ETA, no repetition, correct emergency routing, and several security/jailbreak checks) graded a 30-case golden set at 30/30. A separate identity-resolution stress test hit 90/100 first-try matches, using real database lookups rather than keyword guessing. A later, larger routing-classification sweep (100 simulated calls) caught 39 real misroutes; about two-thirds were genuinely fixed and re-verified live against the judge — but the "fully reconciled" report that followed was assembled with hardcoded, unverified pass marks for a meaningful share of the rest, so it isn't a trustworthy 100%. More on that below.

**What's confirmed absent from both codebases, not just unmentioned:** sentiment scoring (nothing computes it — the only related code is a comment explaining why Twilio's profanity filter is *disabled*, so the model can read real emotional language rather than "****"), a completion-rate metric, and any check that a promise actually gets kept. Stage 3 of the script promises a callback "within 2–3 hours"; no code path anywhere schedules it. A call can pass every rubric check while the promise it made goes unkept. Compliance and outcome are being conflated, and only the first one is instrumented.

## The eval harness and dashboard

The harness (`rubric.py` + `run_eval.py`) grades simulated transcripts against tagged rules using `gpt-5` as an independent judge model, deliberately separate from the `gpt-4o-mini` agent under test. Worth naming honestly: both are OpenAI models, so a same-family self-grading bias risk remains — accepted, not solved.

The concrete story of the harness earning its keep: a 100-query routing-classification sweep found 39 real failures — 17 fell back to a generic, unnamed escalation; 12 named the wrong team; 10 resolved the issue without escalating at all. Roughly two-thirds were genuinely fixed and re-verified live, and the fix even grew new deterministic misroute-correction logic in the production agent. But building the final "100% passing" report exposed a second, more interesting bug: the compile script hardcoded a pass verdict into every row instead of reading back a real result, and at least 23 of the 100 rows carry a pass with either a documented real fail as their last actual grade, or no supporting re-grade at all. The expected "correct" team for several cases was also redefined mid-investigation, which mechanically inflates a pass rate independent of whether the agent improved. State it plainly: an eval pipeline that can silently report "fully passing" without re-checking every case is a real product risk, not a cosmetic one — arguably a sharper finding than the routing bugs it was built to catch.

The dashboard side came later, and from a different codebase — internally called the Vetic dashboard, and rebranded "Meridian" only in its user interface (the builder's own progress log never uses that name, describing it instead as "the persistence + dashboard layer [that] has always been missing"). It's a real product: a Postgres-backed Next.js app with a call-history view, a per-turn latency timeline, and an `/evals` section that consumes this same harness's results, reached in development through a fixed, reserved ngrok domain. Checking it live turned up 117 real call records already logged — more than the project's own notes describe — but also a real inconsistency worth naming: its public-facing marketing pages show hardcoded claims like "92%+ First-Contact Resolution" that aren't computed from anything in that same database.

## Key tradeoffs

**Model cost vs. latency.** `gpt-5.4-nano` listed a lower input price than `gpt-4o-mini`, but a real, timed 100-query run told a different story: 5.46M prompt tokens at a 96.7% cache-hit rate in about 14 minutes on `gpt-4o-mini`, versus a `gpt-5.4-nano` run killed partway through for running far longer with visibly worse per-call latency — and a higher effective output price. Kept the incumbent.

**Hosting: build vs. shelve.** A working Fly.io deployment branch was built, then abandoned the same day once its lack of a free always-on tier collided with a hard $0-hosting constraint — a real cost decision made against real infrastructure, not a hypothetical one.

**Architecture: ship simple, rebuild on evidence.** The Twilio webhook loop was the right MVP — it shipped fast and proved the conversation design on real calls — but it's a request/response loop dressed as a phone call. The rewrite to LiveKit's real-time architecture only happened once the MVP's own latency data justified it.

**Scope cut: compliance over outcome.** No system was ever built to verify that a promised callback actually happens. The harness grades promise-*keeping language*, not promise-keeping fact — a deliberate, if consequential, scope boundary.

## Evolution: MVP → rebuild → dashboard

The Twilio MVP surfaced its own successor before anyone built it: the same latency document that diagnosed "perceived lag" as the core UX problem also scoped the eventual rewrite, in writing, as "the big win, the big rework." The LiveKit rebuild that followed immediately surfaced new problems the MVP had never been tested against — an emergency-location fallback that never fired after six repeated questions, and a no-ETA rule that existed only in the inbound agent, not outbound — both found by a stress test, fixed the same day, and re-verified with real scripted calls. The dashboard layer came last, and wasn't planned upfront: it was built once the gap of having no persistence anywhere "has always been missing," in the builder's own words — observability as a belated but deliberate product decision, not a day-one one.

## Impact

Being honest about what this project can and can't claim: there's no adoption, cost-saved, or business-impact number anywhere in either codebase — this was never deployed at real call volume. What's real and quantified: a 96.7% prompt-cache hit rate measured on a real 100-query run; a genuine classification-routing regression caught, with roughly two-thirds of the 39 failures confirmed fixed through live re-grading (see above for why it isn't a clean 39-to-zero story); one real security leak found and fixed — a config endpoint that returned an entire `.env` file, secrets included, to any caller, patched with a server-side allowlist; and real calls placed and recorded on both builds, including two real Twilio call recordings with transcripts and a real LiveKit browser test call with measured sub-second time-to-first-audio-byte.

## What I'd change with more time

1. **Build real outcome verification** — not just rule-compliance, but confirmation that a promised callback actually happened.
2. **Resolve the judge's self-grading bias** with a genuinely different model family from the agent under test.
3. **Replace the identity-resolution keyword detector** — it produced its own false positives in testing; a deterministic database check shouldn't need a heuristic layer on top.
4. **Reconcile the dashboard's marketing claims with its own real, computed stats** before anything customer-facing ships.
5. **Fix the eval-reconciliation process itself** — a report should never claim "fully passing" without re-reading a real verdict for every single case. This is as important as fixing the agent.

## Reflection: the PM lens

The clearest product lesson here wasn't in the agent — it was in the measurement. Building an eval harness before proving demand was the right prioritization call under real ambiguity, and the cost/latency decision (rejecting a cheaper-looking model on real timed data instead of vendor headline pricing) is the kind of call that only looks obvious in hindsight. But the eval-integrity finding is the one I'd spend the most interview time on: the gap between "the report says 100%" and "100% of the report is actually true" is exactly the gap a PM is supposed to be paranoid about, in this project as much as in the product it was built to measure.

## Open questions for the author

- Where does the LiveKit agent actually run now that Fly.io hosting was shelved — a permanent host, or still local-only?
- Was there any human/business review of the two real recorded Twilio-era calls, confirming or disputing call quality?
- Is there a target company or role this version should be tailored to, or does it stay general-portfolio?
- Should the dashboard's fabricated marketing-page stats be named as an open issue in the published version, or is that too self-critical for a portfolio piece?
