# Vetic Voice Agent on LiveKit

Real-time voice agent (WebRTC + telephony) replacing the Twilio `<Gather>` pipeline.
Fixes the Twilio build's three problems — STT accuracy, latency, and turn-taking — by
streaming **Sarvam STT → Claude → Sarvam TTS** with LiveKit handling transport and
turn-taking. Uses the **official `livekit-plugins-sarvam`** plugin for both STT and TTS
(no hand-built adapters), so Sarvam understands Indian English and keeps the "Aarya" voice.

The old Twilio server in `../twilio_test/` is untouched and still works as a fallback.

```
livekit_agent/
  agent.py          # AgentSession: Sarvam STT + Claude LLM + Sarvam TTS; call flow + DB
  vetic_db.py       # customer-record lookup (same Excel DB as the Twilio server)
  requirements.txt
  .env.example
```

## 1. Prerequisites
- **Python 3.10+**
- A **LiveKit Cloud** project → https://cloud.livekit.io (free tier): `LIVEKIT_URL`, API key, API secret.
- Your existing **Sarvam** and **Anthropic** keys. (No Deepgram — Sarvam does STT now.)

## 2. Setup
```bat
cd C:\Users\hp\Documents\claude_WO_API\livekit_agent
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

copy .env.example .env
REM then edit .env: LiveKit (x3) + SARVAM_API_KEY + ANTHROPIC_API_KEY
```

## 3. Run the agent worker
```bat
python agent.py dev
```
Connects your agent to your LiveKit Cloud project and waits for a room/call.

## 4. Test it (no phone needed)
Open the **LiveKit Agents Playground**: https://agents-playground.livekit.io
- Connect it to the **same** LiveKit Cloud project → **Connect** → allow the mic.
- Aarya greets you. **Use headphones** for the cleanest test.

## 5. Telephony (later)
Point a SIP trunk (Twilio/Plivo/Exotel) at LiveKit: inbound trunk + dispatch rule routes
calls to a room the agent joins; outbound via `CreateSIPParticipant`. The agent code here
is unchanged — only the transport differs.

## Required environment variables
| Variable | Needed | Notes |
|---|---|---|
| `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` | ✅ all three | from LiveKit Cloud |
| `SARVAM_API_KEY` | ✅ | does **STT and TTS** |
| `ANTHROPIC_API_KEY` | ✅ | the LLM |
| ~~`DEEPGRAM_API_KEY`~~ | ❌ | not used |

Everything else (`TEST_CALL_IDENTIFIER`, `TICKET_DB_PATH`, `LLM_MODEL`, the `SARVAM_STT_*`
and `SARVAM_*`/TTS vars) has a default — see `.env.example`.

## What was ported vs. changed
- **Ported:** the Aarya persona + tone rules, the 4-phase call flow (intro → listen →
  resolution → conclusion), and DB personalization (`vetic_db.get_user_context`, same
  Excel + fields: owner/pet/service/date/clinic/city).
- **STT + TTS are now Sarvam** via the official plugin: `saaras:v3` (transcribe) for STT —
  built for Indian English, the core fix for the garbled Twilio transcripts — and
  `bulbul:v3` / `priya` for TTS (the same Aarya voice). Sarvam STT does its own VAD, so
  `AgentSession(turn_detection="stt")` is used with no separate VAD plugin.
- **Changed:** the spoken control-tag state machine (`[NEXT_STAGE]`/`[END_CALL]`) is
  replaced by one consolidated call-flow prompt + an `end_call` **function tool**. A
  stricter staged version can later use LiveKit multi-agent handoffs.

## Notes
- I haven't run this end-to-end here (needs your LiveKit keys + `pip install`). It follows
  Sarvam's official LiveKit example, so it should run; if `python agent.py dev` errors,
  paste the traceback and I'll fix it fast.
- Versions are pinned to `~=1.2` to match the current Sarvam plugin. If you bump
  `livekit-agents`, keep `livekit-plugins-sarvam` on the same minor.
