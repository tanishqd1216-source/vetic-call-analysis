# Latency Optimization — Vetic Calling Agent

Pipeline per turn: `STT → /process_speech → Claude → Sarvam TTS → <Play>`.

First principle: **measure before optimizing.** In a phone call the perceived
lag is what makes the agent feel robotic. Find the one dominant cost, then fix it.

## 0. Measure first (do this before anything else)
Log a timestamp at each step in `/process_speech` — speech received, Claude
returned, TTS returned, response sent — and print the deltas. You'll usually find
**one** dominant cost (often Sarvam TTS or Twilio endpointing, not Claude).
Optimize that one.

## Highest leverage

**1. Skip Claude AND TTS for confident intents.**
You already do this for end-call keywords (`end_keywords` fast-track). Extend the
pattern: a cheap local intent match against the RAG `triggers`; on a confident
hit, return the canned `response` with **pre-rendered audio** — zero LLM, zero
TTS, instant. The RAG responses are fixed strings, so this is a natural fit and
removes both network calls on common turns. Biggest single win.

**2. Stream Claude + chunk the TTS.**
Today you wait for the *entire* Claude response, then send the whole thing to
Sarvam — two serial blocking waits. Use `client.messages.stream(...)`, and the
moment the first sentence completes, fire it to Sarvam while Claude keeps
generating. The caller hears audio ~one sentence sooner. (If Sarvam offers a
streaming TTS endpoint, even better; otherwise sentence-chunking gets most of the
benefit.)

**3. Pre-render all fixed phrases to audio.**
The greeting (`"Good morning, this is Aarya…"`), re-prompts (`"Are you still
there?"`, `"I didn't catch that…"`), and goodbyes are re-synthesized by Sarvam on
*every* call. Generate them once, save as static WAVs, serve from disk. Removes a
TTS round-trip on those turns entirely.

## Medium leverage, low effort

**4. Reuse the Sarvam connection + make it non-blocking.**
You create a fresh connection per `requests.post` (new TLS handshake each time)
and it's a synchronous call inside an `async` handler — it blocks the event loop.
Use a module-level `requests.Session()` (or `httpx.AsyncClient`) to keep the
connection warm and stop blocking concurrent calls.

**5. Tune Twilio speech endpointing.**
`speechTimeout="auto"` lets Twilio decide when the caller stopped talking — often
the largest *perceived* delay. An explicit value (e.g. `speechTimeout="1"`) can
shave a second, at the risk of cutting off slow talkers. `bargeIn=True` is already
set (good — caller can interrupt). Worth A/B testing.

**6. Keep everything warm and co-located.**
Pool the Postgres connection (don't reconnect per request), keep the process warm
(no cold starts), host the server in a region close to Twilio + Sarvam. The
`Anthropic` client is already module-scoped — good.

## Situational / measure first

**7. Prompt caching + trim history.**
Cache the stable system prefix (BASE_RULES + stage rules + examples) so input
processing is cheaper. **Caveat:** Haiku 4.5's minimum cacheable prefix is ~4096
tokens — your prompt is likely below that, so caching may silently not engage.
Verify with `usage.cache_read_input_tokens` before relying on it. On long calls,
trimming old turns from `history` reduces input tokens and time-to-first-token.

**8. Don't bother lowering `max_tokens`.**
It's a ceiling, not a cost — generation stops at `end_turn` regardless. Keep
thinking **off** (you already do); Haiku doesn't add thinking latency by default.
You're already on the fastest model tier, so there's no faster model to switch to.

## Big win, big rework

**9. Twilio Media Streams (WebSocket).**
The current Gather→Play loop makes Twilio fetch each audio file over HTTP — an
extra round trip per turn. Real-time bidirectional audio over Media Streams
eliminates that and enables true streaming STT/TTS. Lowest-latency architecture
but a substantial rewrite — only worth it once the cheaper wins above are
exhausted.

---

## Suggested order of attack
1. Instrument (#0) to confirm the bottleneck.
2. Implement the trio #1–#3 (intent fast-path + audio pre-render cache + streaming/chunked TTS).
3. Quick wins #4–#6.
4. Revisit #7 only if measurements justify it.
5. Consider #9 (Media Streams) only after the above.
