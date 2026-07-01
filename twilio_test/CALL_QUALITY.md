# Call Quality — Work Log & Notes

Personal notes on the Vetic calling agent (`server.py`). Problems that were hurting
call quality, ranked highest → lowest impact, with what was done to fix each.

**Pipeline per turn:** caller speaks → Twilio STT → `POST /process_speech` → Claude
(streamed) → Sarvam TTS → `<Play>` inside a barge-in `<Gather>` → repeat. 4-stage
state machine: Introduction → Context → Resolution → Conclusion.

Status: ✅ done · ⬜ open

---

## Problems & resolutions (highest → lowest impact)

| # | Problem | Fix | Where | Status |
|---|---------|-----|-------|--------|
| 1 | **Dead air.** AI then TTS ran serially (two blocking waits); blocking `requests` froze the event loop; stalls caused 16–24s silence. | Stream Claude (`AsyncAnthropic`) + synthesize each sentence concurrently as it arrives (TTS overlaps generation); async `httpx` over a warm shared connection; `LLM_TIMEOUT`/`SARVAM_TIMEOUT` + graceful "bear with me" fallback. | `_stream_and_synthesize`, `synthesize`, `_twiml_trouble` | ✅ |
| 2 | **RAG built but unused.** `knowledge/rag.py` + `grooming_intro.json` never wired in. | Fast-path: confident intent match → play a clip pre-rendered at startup (zero LLM + zero TTS). Also inject few-shot examples into the system prompt. | `match_intent`, `_try_fast_path`, `_prerender_intents`, `build_examples_block` | ✅ |
| 3 | **Hardcoded demo scenario** ("angry Rajat / bleeding pet / refund") injected into every call, overriding the real customer record. | Gated behind `DEMO_MODE` env flag, **OFF by default**. | `DEMO_MODE`, `_init_conversation` | ✅ |
| 4 | **Greeting ↔ script mismatch.** Greeting asked about timing; stage-1 script assumed it asked the name. | Greeting now confirms identity by name (from DB); stage-1 prompt rewritten to match. | `_opening_greeting`, `prompts/gemini/1_introduction.txt` | ✅ |
| 5 | **No barge-in** in the main server (caller couldn't interrupt). | Consolidated 3 servers → 1; audio nested inside `<Gather bargeIn=True>`. | `build_gather`, `_append_listen_with_fallbacks` | ✅ |
| 6 | **Pace fought the tone.** One server spoke 15% faster, contradicting "calm for upset callers". | Dynamic per-stage pacing; slows further for distressed callers. | `STAGE_PACE`, `get_pace`, `is_distressed` | ✅ |
| 7 | **Speech recognition / endpointing.** Default ASR model, slow `speechTimeout="auto"`, profanity masking. | `speechModel="phone_call"` + `enhanced`; `speechTimeout` tuned (env `SPEECH_TIMEOUT`, default 2s); `profanityFilter=false`. | `build_gather` | ✅ |
| 8 | **Latency/cost creep on long calls.** Full transcript resent every turn. | History trimming (send last `HISTORY_MAX_MESSAGES`, default 16) + prompt caching of the system prefix. | `_messages_for_api`, `_stream_and_synthesize` | ✅ |
| 9 | **Fragile state machine.** Missing `[NEXT_STAGE]`/`[END_CALL]` tag → call stuck looping a stage / never ends. | Plan: per-stage turn-count safety net + hard max-turns hangup. | — | ⬜ |
| 10 | **Dead model routing + in-memory store.** `is_complex()` routes to the same model (no-op); `conversations` dict lost on restart, single-worker. | Plan: route hard turns to Sonnet; durable conversation store. | — | ⬜ |

---

## Grouping
- **Latency:** #1 (stream/non-block/timeout), #2 (fast-path), #7 (endpointing), #8 (trim+cache).
- **Conversation correctness:** #3 (demo gate), #4 (greeting↔script), #9 (state machine — open).
- **Naturalness:** #5 (barge-in), #6 (calm pacing), #7 (fewer mis-hears).
- **Codebase health (enabled the above):** consolidated 3 servers → 1 `server.py`; deleted dead files (`server_hybrid*.py`, `server_backup.py`, `generate_greeting.py`, `config.yml`); fixed `requirements.txt`; standardized on the `venv` environment (`.venv` removed).

---

## Tunable env knobs added
| Var | Default | Purpose |
|-----|---------|---------|
| `LLM_TIMEOUT` | 12 | Claude request timeout (s) |
| `SARVAM_TIMEOUT` | 8 | Sarvam TTS timeout (s) |
| `SPEECH_MODEL` | `phone_call` | Twilio ASR model |
| `SPEECH_TIMEOUT` | `2` | Trailing-silence end-of-speech (s); `auto` = laggier but safer |
| `START_TIMEOUT` | 15 | Wait for caller to start speaking (s) |
| `HISTORY_MAX_MESSAGES` | 16 | Transcript window sent to Claude |
| `PROMPT_CACHING` | on | Cache the system prefix |
| `DEMO_MODE` | off | Inject the hardcoded test scenario |
| `CAMPAIGN_SCENARIO` | `grooming` | Fallback scenario for fast-path / few-shots |

---

## Known limits / caveats
- **Prompt caching may be dormant on Haiku.** Min cacheable prefix ≈ 2048 tokens (Haiku); current system prompt is ~1k–1.3k tokens, so it likely won't engage until the prompt grows or model = Sonnet. Verify via the per-turn log (`cache read=…`).
- **`speechTimeout=2` trade-off.** Snappier, but a distressed caller pausing ~2s could be cut off → bump `SPEECH_TIMEOUT=3`/`auto` if testing shows it.
- **Twilio webhook ceiling.** Audio only plays after the full TwiML response returns; true real-time (audio out while AI still talking) needs Media Streams (WebSocket) — a separate, larger rewrite.
- **Run from `twilio_test/` using `venv`** (it has `openpyxl`; without it the Excel DB silently fails to load → no personalization).

## Verify in logs
Each LLM turn prints, e.g.:
`[LATENCY] LLM 1.2s | TTS-tail 0.3s | clips=2 | hist 16/28 sent | cache read=1200 created=0`
- `TTS-tail` = TTS time NOT overlapped with streaming (lower = streaming working).
- `hist X/Y sent` = trimming active.
- `cache read=` = prompt caching actually engaging (>0 = yes).

## Still open (next)
1. ⬜ State-machine safety net (#9) — stuck/endless-call guard.
2. ⬜ Model routing + durable store (#10).
3. ⬜ Media Streams (WebSocket) — lowest-latency architecture, big rewrite.
