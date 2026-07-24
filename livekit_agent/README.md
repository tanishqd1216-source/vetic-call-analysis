# Vetic Voice Agent Architecture

The Vetic Voice Agent is a real-time, WebRTC-based telephony AI that acts as a customer service representative. It replaces the legacy Twilio `<Gather>` pipeline, solving critical issues related to Speech-To-Text (STT) accuracy, response latency, and conversational turn-taking.

This document outlines the agent's technical architecture, prompt engineering pipeline, and core business logic.

## 1. Technical Pipeline & Stack

The agent utilizes **LiveKit** to handle transport and turn-taking, passing audio streams between specialized AI models in real-time.

- **Transport**: LiveKit (WebRTC) handles low-latency audio streaming.
- **STT (Speech-to-Text)**: **Sarvam** (`saaras:v3`). We use the official `livekit-plugins-sarvam` plugin. Sarvam is optimized for Indian English, fixing the garbled transcripts seen in standard STT models. It includes its own Voice Activity Detection (VAD).
- **LLM (Logic)**: **`gpt-4o-mini`** (OpenAI), chosen over Claude for lower per-turn cost at this call volume. Optionally routed through a local [Headroom](https://pypi.org/project/headroom-ai/) proxy (`HEADROOM_PROXY_URL`) that compresses the outgoing prompt before forwarding to OpenAI, to cut per-turn token cost further; falls back to calling OpenAI directly if the proxy isn't running.
- **TTS (Text-to-Speech)**: **Sarvam** (`bulbul:v3` / `priya` voice). This maintains the required "Aarya" persona.
- **RAG**: ChromaDB (`knowledge_db/`) for FAQ/policy lookup via a `search_knowledge_base` tool.

Two agent variants share this pipeline: `agent.py` (outbound — customer context is
pre-loaded before the call) and `agent_inbound.py` (inbound — identity-first, asks
for the caller's mobile number and looks it up via a `lookup_customer` tool before
proceeding).

## 2. Prompt Engineering & Call Flow

The core intelligence of the agent lives in `agent.py`. To prevent the LLM from "monologuing" or hallucinating processes, the prompt acts as a strict state machine divided into four distinct phases:

### Phase 1: Introduction
The agent introduces itself as Aarya and authenticates the user by referencing their specific pet and clinic visit dynamically injected from the database.

### Phase 2: Listening & Empathy
The agent is explicitly instructed to let the customer fully explain their issue without interrupting. It must use active listening cues and validate the customer's frustration.

### Phase 3: Resolution Lookup
The prompt contains a massive `RESOLUTION LOOKUP TABLE` mapping user complaints (e.g., Grooming, OPD, Home Delivery, Billing) to strict, step-by-step `ACTION` blocks. 

### Phase 4: The Strict 5-Step Pattern
To ensure a human-like, interactive conversation, the agent executes resolutions using a mandatory 5-step flow:
1. **Offer Resolution**: State the resolution concisely based on the `ACTION` blocks.
2. **Confirm Agreement**: Ask if the customer accepts the resolution (e.g., *"Is that ok for you?"*). Wait for a reply.
3. **Ask for Time**: If the resolution requires scheduling, ask for a time. **CRITICAL**: The agent will not move forward until the customer provides a specific time.
4. **Confirm**: Confirm the booking/time back to the customer.
5. **Wrap Up**: Ask *"Is that all you were looking for or is there anything I can help you with?"* before utilizing the `end_call` tool to disconnect.

## 3. Core Business Logic & Constraints

The prompt strictly enforces several business rules:
- **Zero-Refund Policy**: The agent is hardcoded to deny all monetary refunds, stating: *"As per Vetic's policy, we do not provide monetary refunds."* The *only* exception is if the customer's pet has passed away.
- **No ETAs**: The agent will never promise a "2-3 hour" turnaround SLA. It will state that teams will resolve the issue "shortly" or "internally."
- **No Callbacks Promised**: The agent will not promise that a team will "follow up with you" (to avoid unmet expectations), but rather that they will "investigate and resolve this."
- **No-Repetition Rule**: If a customer denies a resolution twice, the agent will not loop. It will immediately pivot to a default escalation ticket.

## 4. Data Flow & Variables

### Customer Context (`vetic_db.py`)
When a call starts, the agent queries an internal database (matching the Twilio backend) to pull customer context. 
Fields injected into the prompt include: `owner`, `pet`, `service`, `date`, `clinic`, and `city`.

### Required Environment Variables
| Variable | Notes |
|---|---|
| `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` | From your LiveKit Cloud dashboard |
| `SARVAM_API_KEY` | Powers both STT and TTS |
| `OPENAI_API_KEY` | Powers the LLM (`gpt-4o-mini`) |
| `HEADROOM_PROXY_URL` | Optional — points at a local `headroom proxy` instance for prompt compression |
| `TICKET_DB_PATH` | Optional — overrides the default `data/vetic_data.db` sample customer DB |

*(Note: Deepgram is no longer used, as Sarvam handles STT natively. Anthropic/Claude
is not currently in the runtime call path — `ANTHROPIC_API_KEY` is unused by
`agent.py`/`agent_inbound.py` even though `.env.example` still lists it.)*

## 5. Setup & Execution

### Prerequisites
- Python 3.10+
- A LiveKit Cloud project (https://cloud.livekit.io)

### Local Development Setup
```bat
cd C:\Users\hp\Documents\claude_WO_API\livekit_agent
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

# Copy the env template and fill in your keys
copy .env.example .env
```

### Running the Agent
```bat
python agent.py dev
```
This connects your agent to your LiveKit Cloud project.

### Testing
1. Open the **LiveKit Agents Playground**: https://agents-playground.livekit.io
2. Connect it to your LiveKit Cloud project.
3. Allow microphone access.
4. The agent will greet you. (Use headphones for the best audio experience).

## 6. Production Deployment

The worker runs as an always-on container on Fly.io (Mumbai/`bom` region — closest
to Sarvam's servers, the most latency-sensitive leg). `webcall_server.py` is a local
test harness only and is **not** part of this deployment; there is no telephony
integration yet (no SIP trunk/phone number), so a deployed worker currently just
sits registered with LiveKit Cloud, ready to be dispatched into a room by whatever
creates one (today, that's `webcall_server.py` run locally against the same
LiveKit project/keys).

### One-time setup
```bash
fly auth login
fly apps create vetic-voice-agent   # or edit the `app` name in fly.toml first if taken
fly secrets set \
  LIVEKIT_URL=wss://YOUR-PROJECT.livekit.cloud \
  LIVEKIT_API_KEY=... \
  LIVEKIT_API_SECRET=... \
  SARVAM_API_KEY=... \
  OPENAI_API_KEY=...
```

### Deploy
```bash
cd livekit_agent
fly deploy               # needs local Docker
# or, with no local Docker:
fly deploy --remote-only
```

### Switching agent variant
`fly.toml`'s `[env] AGENT_ENTRYPOINT` controls which script `run.py` execs —
`inbound` (default, `agent_inbound.py`) or `outbound` (`agent.py`). Change it and
redeploy, or `fly secrets set AGENT_ENTRYPOINT=outbound` for a one-off override.

### Checking it's alive
```bash
fly status      # machine should show "started" / healthy
fly logs        # look for the worker's LiveKit registration message
```

### Known gaps (tracked in `PROGRESS.md`)
- `data/vetic_data.db` is a static sample customer DB baked into the image, not a
  live connection to Vetic's real Postgres/Mongo — fine for now since there's no
  real call traffic yet, but needs to change before this handles real customers.
- No telephony (SIP trunk / real phone number) — this deployment only proves the
  worker itself is reliable and always-on.
- No log aggregation beyond `fly logs` yet — latency/token metrics are emitted as
  structured `latency_metric ...` log lines (grep-able) but not yet shipped to
  Vetic's existing Grafana/Loki.
