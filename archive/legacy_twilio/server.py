from fastapi import FastAPI, Form, Request, BackgroundTasks
from fastapi.responses import Response, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from twilio.jwt.access_token import AccessToken
from twilio.jwt.access_token.grants import VoiceGrant
from twilio.twiml.voice_response import VoiceResponse, Gather, Play
from twilio.rest import Client
from anthropic import AsyncAnthropic
from dotenv import load_dotenv
import psycopg2
import psycopg2.extras
import asyncio
import os
import re
import pandas as pd
import sqlite3
import time
import csv
import httpx

from knowledge.rag import (
    build_examples_block,
    match_intent,
    split_control_tag,
    all_instances,
    STAGE_NAMES,
)

# ─────────────────────────────────────────────────────────────────────────────
# CANONICAL Vetic calling-agent server.
#
# Consolidates the three former servers into one:
#   * BRAIN          (from old server.py): dynamic per-stage pacing, distress-based
#                     slow-down, speech hints built from the real caller's record,
#                     4-stage state machine.
#   * BARGE-IN       (from server_hybrid_prerender_bargein.py): spoken audio is
#                     nested INSIDE <Gather bargeIn=True> so the caller can interrupt.
#   * PRE-RENDER     fixed fallback phrases synthesized once at startup.
#   * RAG FAST-PATH  (NEW): on a confident intent match we skip Claude AND TTS and
#                     play a clip pre-rendered at startup. Few-shot examples are also
#                     injected into the system prompt for the LLM path.
#   * STREAMING      (#2): the per-turn LLM reply is STREAMED (AsyncAnthropic) and each
#                     sentence is synthesized concurrently (async httpx, warm connection)
#                     as it arrives — so TTS overlaps generation instead of running after
#                     it. Claude + Sarvam both have timeouts and degrade gracefully.
#                     All sentence clips are nested in one barge-in <Gather>.
#   * CONTEXT        (#9b): only a bounded window of the transcript is sent to Claude
#                     each turn (HISTORY_MAX_MESSAGES) and the stable system prefix is
#                     prompt-cached, so latency/cost stay flat on long or looping calls.
# ─────────────────────────────────────────────────────────────────────────────

load_dotenv()

app = FastAPI()

# Fixed fallback phrases — pre-rendered once at startup (see startup_event).
STILL_THERE_TEXT = "Are you still there? Please let me know if you need help."
GOODBYE_TEXT = "I didn't hear anything. I will connect to you again after some time."
TROUBLE_TEXT = "I'm so sorry, please bear with me for just a moment."
FALLBACK_STILL_THERE = None
FALLBACK_GOODBYE = None
FALLBACK_TROUBLE = None

# Timeouts (seconds) so a stalled API becomes a 1-line hiccup, not 20s of silence.
LLM_TIMEOUT = float(os.environ.get("LLM_TIMEOUT", "12"))
SARVAM_TIMEOUT = float(os.environ.get("SARVAM_TIMEOUT", "8"))

# Conversation history sent to Claude each turn is capped to the last N messages so
# the prompt (and thus latency + cost) stays flat on long or looping calls. The full
# transcript is kept in memory; only the view sent to the model is trimmed. Durable
# context (customer record + stage rules) lives in the system prompt, so dropping old
# turns is safe. PROMPT_CACHING caches that stable system prefix across turns.
HISTORY_MAX_MESSAGES = int(os.environ.get("HISTORY_MAX_MESSAGES", "16"))
PROMPT_CACHING = os.environ.get("PROMPT_CACHING", "1").strip().lower() in ("1", "true", "yes", "on")

# instance_id -> pre-rendered wav filename (filled at startup by _prerender_intents).
PRERENDERED_INTENTS: dict[str, str] = {}

# Outbound campaign this agent is running. Used as the fallback scenario for both
# few-shot injection and the fast-path, so stage-1 turns can match knowledge before
# a scenario keyword (bath/groom/...) has appeared in the conversation.
CAMPAIGN_SCENARIO = os.environ.get("CAMPAIGN_SCENARIO", "grooming")

# DEMO scenario — a hardcoded angry-customer situation for testing. It OVERRIDES the
# real customer's record, so it is OFF by default and only injected when DEMO_MODE is
# enabled (set env DEMO_MODE=1). Real calls use only the genuine database context.
DEMO_MODE = os.environ.get("DEMO_MODE", "").strip().lower() in ("1", "true", "yes", "on")
# Which DB record the browser test page (/voice) loads. Defaults to a real unhappy-GROOMING
# ticket so the browser test exercises the grooming win-back flow. Override via env to test
# another customer (matches a Ticket ID, phone number, or row index in the ticket DB).
TEST_CALL_IDENTIFIER = os.environ.get("TEST_CALL_IDENTIFIER", "352027")
# Record browser test calls (the /voice path) so they can be reviewed afterwards.
# The outbound make_call path already records via record=True; this covers browser calls.
RECORD_BROWSER_CALLS = os.environ.get("RECORD_BROWSER_CALLS", "1").strip().lower() in ("1", "true", "yes", "on")
DEMO_SCENARIO_CONTEXT = (
    "\n\nCRITICAL SCENARIO CONTEXT:\n"
    "The user is Rajat. He is extremely angry about his last grooming session. "
    "His pet got a cut near the neck during grooming and is bleeding. He wants to "
    "take the pet to the clinic to see a doctor immediately and wants a full refund "
    "for the grooming service. Aarya MUST be highly empathetic, apologize profusely, "
    "and prioritize getting the pet to the clinic while assuring him the refund will "
    "be processed."
)


@app.on_event("startup")
async def startup_event():
    global FALLBACK_STILL_THERE, FALLBACK_GOODBYE, FALLBACK_TROUBLE
    FALLBACK_STILL_THERE, FALLBACK_GOODBYE, FALLBACK_TROUBLE = await asyncio.gather(
        synthesize(STILL_THERE_TEXT),
        synthesize(GOODBYE_TEXT),
        synthesize(TROUBLE_TEXT),
    )
    await _prerender_intents()


async def _prerender_intents():
    """Synthesize every knowledge-base canned response to audio ONCE at startup,
    so a fast-path hit costs zero TTS. Each clip is rendered at its stage's pace.
    All clips are synthesized concurrently to keep boot fast."""
    stage_num = {name: num for num, name in STAGE_NAMES.items()}
    items = []
    for _scenario, stage_name, inst in all_instances():
        clean, _tag = split_control_tag(inst.get("response", ""))
        if clean:
            items.append((inst["id"], clean, get_pace(stage_num.get(stage_name, 1))))
    results = await asyncio.gather(
        *[synthesize(text, pace=pace) for _id, text, pace in items]
    ) if items else []
    count = 0
    for (iid, _t, _p), fn in zip(items, results):
        if fn:
            PRERENDERED_INTENTS[iid] = fn
            count += 1
    print(f"[startup] Pre-rendered {count} intent audio clip(s).")


@app.on_event("shutdown")
async def shutdown_event():
    """Close the shared async clients cleanly on restart/stop."""
    try:
        if _http_client is not None:
            await _http_client.aclose()
    except Exception:
        pass
    try:
        await aclient.close()
    except Exception:
        pass


os.makedirs("audio", exist_ok=True)
app.mount("/audio", StaticFiles(directory="audio"), name="audio")

aclient = AsyncAnthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"), timeout=LLM_TIMEOUT)
DATABASE_URL = os.environ.get("DATABASE_URL")
SARVAM_API_KEY = os.environ.get("SARVAM_API_KEY")

# Shared async HTTP client for Sarvam — keeps the TLS connection warm (no per-call
# handshake) and never blocks the event loop. Created lazily inside the loop.
SARVAM_URL = "https://api.sarvam.ai/text-to-speech"
_http_client: httpx.AsyncClient | None = None


def _http() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(timeout=httpx.Timeout(SARVAM_TIMEOUT))
    return _http_client

# --- Load SQLite Database ---
DB_PATH = r"c:\Users\hp\Documents\sample data\vetic_data.db"
try:
    conn = sqlite3.connect(DB_PATH)
    TICKET_DB = pd.read_sql("SELECT * FROM ticket_summary", conn)
    conn.close()
    print(f"Loaded {len(TICKET_DB)} records from ticket database.")
except Exception as e:
    print(f"Failed to load ticket database: {e}")
    TICKET_DB = None


def _phone_digits(x):
    """Normalize any phone format to its last 10 digits for matching."""
    s = str(x)
    if s.endswith(".0"):              # in case pandas reads the number as a float
        s = s[:-2]
    d = re.sub(r"\D", "", s)          # keep digits only
    return d[-10:] if len(d) >= 10 else d


def get_user_context(identifier=0) -> str:
    """Return ONE customer's record, formatted for the agent to verify on the call.

    identifier: int          -> row index (demo)
                phone digits  -> matched against 'mobile No.'
                other string  -> matched against 'Ticket ID', then 'user_ids'
    Only the matched customer is injected (not the whole database).
    """
    if TICKET_DB is None or TICKET_DB.empty:
        return ""
    try:
        row = None
        if isinstance(identifier, int):
            if 0 <= identifier < len(TICKET_DB):
                row = TICKET_DB.iloc[identifier]
        else:
            ident = str(identifier).strip()
            target = _phone_digits(ident)
            if len(target) == 10 and "mobile_no." in TICKET_DB.columns:
                hits = TICKET_DB[TICKET_DB["mobile_no."].map(_phone_digits) == target]
                if not hits.empty:
                    row = hits.iloc[0]
            if row is None:
                for col in ("ticket_id", "user_ids"):
                    if col in TICKET_DB.columns:
                        hits = TICKET_DB[TICKET_DB[col].astype(str).str.strip() == ident]
                        if not hits.empty:
                            row = hits.iloc[0]
                            break
        if row is None:
            return ""

        def g(*keys):
            for k in keys:
                if k in row:
                    v = str(row[k]).strip()
                    if v and v.lower() != "nan":
                        return v
            return None

        parts = [
            "CUSTOMER RECORD ON FILE (the person you are calling).",
            "VERIFY each detail with the customer in your own words; do NOT assume it is correct until they confirm:",
        ]
        for label, val in [
            ("Owner name", g("full_name")),
            ("Pet name", g("pet_name")),
            ("Service / appointment type", g("final_app_type", "appt_type", "type")),
            ("Appointment date", g("appointment_date")),
            ("Clinic", g("final_clinic_name")),
            ("City", g("city")),
        ]:
            if val:
                parts.append(f"- {label}: {val}")
        return "\n".join(parts)
    except Exception as e:
        print(f"Error fetching user context: {e}")
        return ""


# ── Voice tone / prosody ─────────────────────────────────────────
# Calm, warm, unhurried delivery for distressed callers.  pace < 1.0 = slower/calmer.
BASELINE_PACE = 0.95

# Tone arc: gentle -> soft & patient -> warm & confident -> reassuring
STAGE_PACE = {
    1: 1.0,   # Introduction   – clear, professional, not rushed
    2: 0.9,   # Context/Listen – slowest, patient, "I have time for you"
    3: 1.0,   # Resolution     – warm but confident / decisive
    4: 0.97,  # Conclusion     – warm, reassuring close
}


def is_distressed(user_speech: str) -> bool:
    return any(kw in user_speech.lower() for kw in (
        "angry", "furious", "ridiculous", "unacceptable", "worst", "terrible",
        "horrible", "refund", "bleeding", "hurt", "sue", "complaint", "never again"))


def get_pace(stage: int, distressed: bool = False) -> float:
    pace = STAGE_PACE.get(stage, BASELINE_PACE)
    if distressed:                 # upset/angry -> slow down further to de-escalate
        pace = round(pace - 0.08, 2)
    return pace


async def synthesize(text: str, pace: float = None, filename: str = None) -> str:
    """Async, non-blocking Sarvam TTS over a warm shared connection. Returns the wav
    filename, or None on timeout/error (callers degrade gracefully). Safe to launch
    many concurrently — that is how sentence chunks overlap LLM generation."""
    import uuid, base64
    text = re.sub(r'[*_\[\]()~]', '', text).replace('"', '').replace('\n', ' ').strip()
    if not text or not SARVAM_API_KEY:
        return None
    if pace is None:
        pace = BASELINE_PACE
    headers = {"api-subscription-key": SARVAM_API_KEY, "Content-Type": "application/json"}
    payload = {"inputs": [text], "target_language_code": "en-IN", "speaker": "priya",
               "pace": pace, "speech_sample_rate": 8000, "enable_preprocessing": True,
               "model": "bulbul:v3"}
    try:
        response = await _http().post(SARVAM_URL, json=payload, headers=headers)
    except Exception as e:
        print(f"[TTS] Sarvam error/timeout (len={len(text)}): {e}")
        return None
    if response.status_code != 200:
        print(f"[TTS] Sarvam returned status {response.status_code}")
        return None
    audio_base64 = response.json().get("audios", [""])[0]
    if not audio_base64:
        return None
    if not filename:
        filename = f"{uuid.uuid4()}.wav"
    with open(os.path.join("audio", filename), "wb") as f:
        f.write(base64.b64decode(audio_base64))
    return filename


# Sentence boundary for streaming chunking: end punctuation + optional closing
# quote/paren/bracket, followed by whitespace.
_SENTENCE_END = re.compile(r'[.?!]["\')\]]?\s')


def _clean_for_tts(text: str) -> str:
    """Strip control tags so they are never spoken (the raw text still carries them
    for the state machine to parse)."""
    return text.replace("[NEXT_STAGE]", "").replace("[END_CALL]", "").strip()


def _messages_for_api(history: list, max_messages: int = None) -> list:
    """Return the trailing window of the transcript to send to Claude, bounded to
    `max_messages` so the prompt can't grow without limit on long calls. The window
    is shifted to begin on a 'user' turn (the Anthropic API requires the first message
    to be the user's). The stored `history` itself is never mutated."""
    if max_messages is None:
        max_messages = HISTORY_MAX_MESSAGES
    if max_messages <= 0 or len(history) <= max_messages:
        return history
    window = history[-max_messages:]
    while window and window[0]["role"] != "user":
        window = window[1:]
    return window


async def _stream_and_synthesize(model: str, system_prompt: str, history: list,
                                 pace: float, metrics: dict):
    """Stream Claude; as each complete sentence arrives, fire its TTS concurrently so
    synthesis OVERLAPS generation instead of starting after it. Sends only a bounded
    history window and (optionally) caches the stable system prefix. Returns
    (full_text, [wav filenames, in spoken order])."""
    tts_tasks = []
    buf, full = "", ""

    api_messages = _messages_for_api(history)
    metrics["history_sent"] = len(api_messages)
    metrics["history_total"] = len(history)
    # Cache the stable system prefix so repeated turns within a stage reuse it.
    system_param = (
        [{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}]
        if PROMPT_CACHING else system_prompt
    )

    llm_start = time.time()
    try:
        async with aclient.messages.stream(
            model=model, max_tokens=300, system=system_param, messages=api_messages,
        ) as stream:
            async for delta in stream.text_stream:
                buf += delta
                full += delta
                while True:
                    m = _SENTENCE_END.search(buf)
                    if not m:
                        break
                    sentence, buf = buf[:m.end()].strip(), buf[m.end():]
                    clean = _clean_for_tts(sentence)
                    if clean:
                        tts_tasks.append(asyncio.create_task(synthesize(clean, pace)))
            # Capture cache/usage stats — wrapped so a failure here never breaks the turn.
            try:
                u = (await stream.get_final_message()).usage
                metrics["cache_read"] = getattr(u, "cache_read_input_tokens", 0) or 0
                metrics["cache_created"] = getattr(u, "cache_creation_input_tokens", 0) or 0
                metrics["input_tokens"] = getattr(u, "input_tokens", 0) or 0
            except Exception:
                pass
    except Exception:
        for t in tts_tasks:
            t.cancel()
        raise
    metrics["llm_latency"] = time.time() - llm_start

    tail = _clean_for_tts(buf)
    if tail:
        tts_tasks.append(asyncio.create_task(synthesize(tail, pace)))

    # Time spent waiting for TTS AFTER the stream ended — captures only the
    # non-overlapped tail, so the CSV shows the streaming win.
    tts_wait = time.time()
    files = await asyncio.gather(*tts_tasks) if tts_tasks else []
    metrics["tts_latency"] = time.time() - tts_wait
    return full, [f for f in files if f]


def is_complex(user_speech: str, history: list) -> bool:
    if any(kw in user_speech.lower() for kw in ("why", "emergency", "urgent", "vomit")):
        return True
    return False


def detect_scenario(text: str, history: list) -> str:
    full_text = text.lower() + " ".join(
        m["content"] if isinstance(m["content"], str) else ""
        for m in history
    ).lower()
    if any(kw in full_text for kw in ("bath", "groom", "haircut", "smell")):
        return "grooming"
    elif any(kw in full_text for kw in ("doctor", "vet", "opd", "medicine", "clinic")):
        return "opd"
    return "general"


def resolve_scenario(state: dict, user_speech: str) -> str:
    """Sticky scenario resolution. detect_scenario can flip back to 'general' on a
    neutral turn; we never downgrade, and default an unknown scenario to the active
    campaign so the knowledge base is reachable from turn one. Returns the effective
    scenario and stores it on state."""
    detected = detect_scenario(user_speech, state.get("history", []))
    current = state.get("scenario", "general")
    if detected != "general":
        current = detected                # upgrade to a specific scenario, sticky
    elif current == "general":
        current = CAMPAIGN_SCENARIO        # default unknown -> campaign scenario
    state["scenario"] = current
    return current


ROUTINE_MODEL = "claude-haiku-4-5-20251001"
COMPLEX_MODEL = "claude-haiku-4-5-20251001"

BASE_RULES = """
You are Aarya, a warm, empathetic, and welcoming AI customer support agent for Vetic Pet Care.
PURPOSE: This is an OUTBOUND service-recovery (win-back) call. You are calling a customer who gave UNHAPPY FEEDBACK on a recent service — for now, an unhappy GROOMING experience. The customer's record is already provided to you below (owner name, pet name, service, appointment date, clinic, city). Your job is to confirm what you already know, understand exactly what went wrong with the grooming, and make it right. You already have their details — you are here to CONFIRM and RESOLVE, never to collect information.
Maintain this warm and empathetic tone throughout the entire conversation.
The customer is likely unhappy or distressed. DO NOT sound joyful, cheerful, or overly enthusiastic. Maintain a calm, serious, and supportive tone throughout the entire conversation.
Your responses will be spoken aloud to a customer on a phone call using an Indian voice.
Keep answers brief and conversational.

CRITICAL RULES:
- Speak ONLY in English. Do not use Hindi words or script.
- Do not use filler sounds like "Umm", "Uhh", "Ah", or "Aaa". They cause the voice engine to stutter.
- Never exceed 2 short sentences per response. Keep it tight — long replies are slow to speak (each sentence adds TTS time) and easy to mishear.
- ABSOLUTELY NO markdown, asterisks, brackets, quotes, or emojis. Output plain text ONLY. (e.g. no *sigh* or [pause])
- IMPORTANT COMPLAINT HANDLING: The customer is likely calling because they are unhappy with the service they received. You must listen patiently, apologize sincerely on behalf of Vetic, and focus on de-escalating the situation. Assure them their feedback is taken seriously and offer a concrete resolution or escalation to management. Maintain your warm and empathetic tone while handling complaints.
- EMPATHY RULE: DO NOT ever use the word "appreciate" (e.g., "I appreciate your feedback", "I appreciate you telling me"). Instead, express genuine empathy using phrases like "I understand completely", "I'm so sorry you experienced that", or "That sounds really difficult".
- ANTI-HALLUCINATION RULE: Do not make up facts, procedures, policies, or solutions. Stick strictly to the context and flow provided. If you don't know something, do not guess; simply state that you will have a manager reach out to assist them.
- Do not list options like a robot; speak naturally.
- Only answer what was asked. Wait for the user to respond before offering more info.
- VERIFICATION RULE: For any detail regarding the user (like pet name, appointment type, clinic, etc.), always verify it from the provided database context. NEVER ask the user for details that are already available in the context.
"""


def get_system_prompt(scenario: str, stage: int, user_context: str = "") -> str:
    prompt_file = ""
    if stage == 1: prompt_file = "prompts/gemini/1_introduction.txt"
    elif stage == 2: prompt_file = "prompts/gemini/2_context.txt"
    elif stage == 3: prompt_file = "prompts/gemini/3_resolution.txt"
    elif stage == 4: prompt_file = "prompts/gemini/4_conclusion.txt"

    with open(prompt_file, "r") as f:
        stage_rules = f.read()

    prompt = BASE_RULES + "\n\n"
    if user_context:
        prompt += user_context + "\n\n"
    prompt += stage_rules

    # NEW: inject curated few-shot examples for this (scenario, stage) if any exist.
    examples = build_examples_block(scenario, stage)
    if examples:
        prompt += "\n" + examples
    return prompt


conversations: dict[str, dict] = {}

# ── Speech recognition (what the agent HEARS) ────────────────────
BASE_HINTS = "Vetic, Aarya, grooming, vaccination, checkup, appointment, refund, clinic, doctor, pet, dog, cat"

# Telephony-tuned ASR + endpointing. All env-overridable so you can A/B test on
# live calls without a code change.
#   SPEECH_MODEL   : 'phone_call' (+ enhanced) is Google's telephony model, far more
#                    accurate on 8 kHz call audio than the default. Alternative:
#                    'experimental_conversations' (no enhanced).
#   SPEECH_TIMEOUT : seconds of trailing silence before Twilio treats the caller as
#                    done. Lower = snappier replies but risks cutting off slow or
#                    emotional talkers; higher = safer but laggier. 'auto' lets Twilio
#                    decide (usually the laggiest). 2s is a balanced start for a
#                    de-escalation line where callers pause to think.
#   START_TIMEOUT  : seconds to wait for the caller to START speaking before re-prompt.
# NOTE: 'phone_call' (and the deprecated 'enhanced' flag) do NOT support en-IN, so
# Indian-accented English was mis-transcribed into Hinglish garbage. The
# 'experimental_conversations' model DOES support en-IN; build_gather() drops the
# invalid 'enhanced' flag automatically for non-phone_call models.
SPEECH_MODEL = os.environ.get("SPEECH_MODEL", "experimental_conversations")
# 2s clipped distressed callers who pause mid-thought; 3s is safer for en-IN.
SPEECH_TIMEOUT = os.environ.get("SPEECH_TIMEOUT", "3")
# Numeric form of SPEECH_TIMEOUT for the perceived-latency log; None when set to 'auto'.
try:
    SPEECH_TIMEOUT_S = float(SPEECH_TIMEOUT)
except ValueError:
    SPEECH_TIMEOUT_S = None
START_TIMEOUT = int(os.environ.get("START_TIMEOUT", "15"))

# Barge-in: when ON (default) the agent's audio is nested INSIDE the <Gather>, so the
# recognizer listens WHILE the agent speaks and the caller can interrupt. The downside
# is that background noise or speaker echo can falsely trigger it, cutting the agent off
# and feeding noise to STT ("the agent can't hear me"). Set BARGE_IN=0 to play the audio
# BEFORE the gather instead — the recognizer then only listens AFTER the agent finishes,
# which is immune to noise/echo cutting in. Use it to A/B test whether barge-in is the
# cause of a "can't hear me" problem.
BARGE_IN = os.environ.get("BARGE_IN", "1").strip().lower() in ("1", "true", "yes", "on")

# With actionOnEmptyResult on, every no-speech turn POSTs to /process_speech (so it gets
# logged), but a truly silent/dead line would then loop "I didn't catch that" forever.
# After this many CONSECUTIVE empty (no-speech) turns, end the call gracefully.
MAX_EMPTY_TURNS = int(os.environ.get("MAX_EMPTY_TURNS", "3"))

# State-machine safety net. A real call once looped ONE stage for ~8 minutes because the
# model never emitted [NEXT_STAGE]. Force-advance a stage after this many turns without
# progress, and hard-cap the whole call so it can never run forever.
MAX_STAGE_TURNS = int(os.environ.get("MAX_STAGE_TURNS", "6"))
MAX_CALL_TURNS = int(os.environ.get("MAX_CALL_TURNS", "25"))

# Drop transcripts below this confidence — they're usually the agent's own echo or background
# noise ("the agent hearing things on its own"), not the caller. 0 disables the gate.
# NOTE: Twilio's experimental_conversations often returns 0.0 for garbage AND for very short
# valid words, so a real one-word reply may occasionally get re-prompted; the Tier-2 Sarvam
# STT swap gives reliable confidence to lean on.
CONFIDENCE_MIN = float(os.environ.get("CONFIDENCE_MIN", "0.3"))


def hints_from_context(user_context: str) -> str:
    """Pull the caller's real owner/pet/clinic/city names so the speech recognizer
    is primed to transcribe them correctly (fixes mis-hears)."""
    terms = []
    for line in (user_context or "").split("\n"):
        for label in ("- Owner name:", "- Pet name:", "- Clinic:", "- City:"):
            if line.startswith(label):
                val = line.split(":", 1)[1].strip()
                if val and val not in terms:
                    terms.append(val)
    return ", ".join(terms)


def _owner_first_name(user_context: str) -> str:
    """First name from the 'Owner name' line of the DB context, for the greeting."""
    for line in (user_context or "").split("\n"):
        if line.startswith("- Owner name:"):
            full = line.split(":", 1)[1].strip()
            return full.split()[0] if full else ""
    return ""


def _opening_greeting(user_context: str) -> str:
    """Build the opening line. Always establishes WHO we're speaking to (identity),
    never asks about timing — stage 1 of the script asks timing next, so this keeps
    the greeting and the script in lockstep."""
    name = _owner_first_name(user_context)
    if name:
        return f"Good morning, am I speaking with {name}? This is Aarya calling from Vetic."
    return "Good morning, this is Aarya calling from Vetic. May I confirm who I'm speaking with?"


def _base_url(request: Request) -> str:
    return request.headers.get("x-forwarded-proto", "http") + "://" + request.headers.get(
        "x-forwarded-host", request.headers.get("host", "127.0.0.1:8000")
    )


def build_gather(extra_hints: str = "") -> Gather:
    """Single source of truth for every speech <Gather>.
    - speechModel='phone_call' + enhanced: Google's telephony-tuned model (much more
      accurate on 8 kHz call audio than the default).
    - profanityFilter off: angry callers' real words reach the model so it reads
      sentiment correctly and de-escalates, instead of seeing masked '****'.
    - bargeIn: caller can interrupt the nested audio.
    - hints: caller's real owner/pet/clinic names primed to reduce mis-hears."""
    hints = BASE_HINTS + (", " + extra_hints if extra_hints else "")
    kwargs = dict(
        input="speech",
        action="/process_speech",
        timeout=START_TIMEOUT,
        speechTimeout=SPEECH_TIMEOUT,
        speechModel=SPEECH_MODEL,
        profanityFilter=False,
        bargeIn=True,
        # Report even no-speech turns to /process_speech, so silence / no-capture is
        # LOGGED (heard=no) instead of silently falling through to hangup. This is what
        # makes a "the agent can't hear me" failure visible in latency_logs.csv.
        actionOnEmptyResult=True,
        language="en-IN",
        hints=hints,
    )
    # `enhanced` is only valid for the phone_call / numbers_and_commands models;
    # leave it off for the experimental models so the combo stays valid.
    if SPEECH_MODEL in ("phone_call", "numbers_and_commands"):
        kwargs["enhanced"] = True
    return Gather(**kwargs)


def _attach_audio(response: VoiceResponse, gather: Gather, base_url: str,
                  play_file: str = None, say_text: str = None):
    """Attach the agent's audio, honoring BARGE_IN.
    BARGE_IN on  -> nested INSIDE the gather (caller can interrupt the agent).
    BARGE_IN off -> played on `response` BEFORE the gather, so the recognizer only
                    listens after the agent finishes (immune to noise/echo cut-ins).
    The caller appends `gather` to `response` afterwards in both cases."""
    target = gather if BARGE_IN else response
    if play_file:
        target.play(f"{base_url}/audio/{play_file}")
    elif say_text:
        target.say(say_text, voice="Polly.Aditi")


def _append_listen_with_fallbacks(response: VoiceResponse, request: Request,
                                  gather: Gather, extra_hints: str = ""):
    """Append the main Gather (caller's turn), then a re-prompt Gather and a final
    goodbye — both PRE-RENDERED at startup. Speech audio is nested inside each Gather
    so the caller can barge in."""
    base_url = _base_url(request)
    response.append(gather)

    gather2 = build_gather(extra_hints)
    _attach_audio(response, gather2, base_url, play_file=FALLBACK_STILL_THERE, say_text=STILL_THERE_TEXT)
    response.append(gather2)

    if FALLBACK_GOODBYE:
        response.play(f"{base_url}/audio/{FALLBACK_GOODBYE}")
    else:
        response.say(GOODBYE_TEXT, voice="Polly.Aditi")
    response.hangup()


async def gather_response(request: Request, say_text: str = None, play_file: str = None,
                          end_call: bool = False, metrics: dict = None, pace: float = None,
                          extra_hints: str = "") -> VoiceResponse:
    """Build a TwiML turn: speak `say_text` (synthesized now) or play `play_file`
    (already on disk, e.g. a pre-rendered intent clip), then listen with barge-in.
    Used for short fixed phrases (re-prompts, goodbyes) and the fast-path; the hot
    LLM turn uses the streaming pipeline (_run_stage) instead."""
    if pace is None:
        pace = BASELINE_PACE
    response = VoiceResponse()
    base_url = _base_url(request)

    if say_text and not play_file and SARVAM_API_KEY:
        tts_start = time.time()
        play_file = await synthesize(say_text, pace=pace)
        if metrics is not None:
            metrics["tts_latency"] = time.time() - tts_start

    if end_call:
        if play_file:
            response.play(f"{base_url}/audio/{play_file}")
        else:
            response.say(say_text, voice="Polly.Aditi")
        response.hangup()
        return response

    # Main turn: speak, then listen. With BARGE_IN the audio is nested in the Gather so
    # the caller can interrupt; without it the audio plays before the Gather.
    gather = build_gather(extra_hints)
    _attach_audio(response, gather, base_url, play_file=play_file, say_text=say_text)

    _append_listen_with_fallbacks(response, request, gather, extra_hints)
    return response


def handle_error(call_sid: str, history: list, e: Exception) -> Response:
    print(f"[{call_sid}] Error: {e}")
    if history and history[-1]["role"] == "user":
        history.pop()
    response = VoiceResponse()
    response.hangup()
    return Response(content=str(response), media_type="application/xml")


async def _build_greeting_twiml(request: Request, call_sid: str) -> Response:
    """Shared opening TwiML for both /incoming_call and /voice: pause, speak an
    identity-confirming greeting (nested in a barge-in Gather), then listen. The
    greeting is built from the caller's DB record so it names them when known."""
    state = conversations[call_sid]
    extra_hints = state.get("extra_hints", "")
    greeting = _opening_greeting(state.get("user_context", ""))
    base_url = _base_url(request)

    response = VoiceResponse()
    response.pause(length=1)

    greeting_file = await synthesize(greeting) if SARVAM_API_KEY else None
    gather = build_gather(extra_hints)
    _attach_audio(response, gather, base_url, play_file=greeting_file, say_text=greeting)

    _append_listen_with_fallbacks(response, request, gather, extra_hints)
    return Response(content=str(response), media_type="application/xml")


def _init_conversation(call_sid: str, identifier) -> dict:
    user_context = get_user_context(identifier)
    state = {
        "history": [],
        "scenario": "general",
        "stage": 1,
        "user_context": user_context,
        "extra_hints": hints_from_context(user_context),
        "metrics": {"llm_latency": 0.0, "tts_latency": 0.0},
    }
    # Demo scenario is injected ONLY when DEMO_MODE is enabled (see note above).
    if DEMO_MODE and DEMO_SCENARIO_CONTEXT:
        print(f"[{call_sid}] DEMO_MODE on — injecting demo scenario (overrides real context).")
        state["user_context"] = user_context + DEMO_SCENARIO_CONTEXT
    conversations[call_sid] = state
    return state


@app.post("/incoming_call")
async def incoming_call(request: Request):
    form_data = await request.form()
    call_sid = form_data.get("CallSid", "")
    # Match the customer by the number we are calling ('To'); fall back to the test number.
    to_number = form_data.get("To") or form_data.get("Called") or ""
    _init_conversation(call_sid, to_number or TEST_CALL_IDENTIFIER)
    return await _build_greeting_twiml(request, call_sid)


def _twiml_play_clips(request: Request, clips: list, end_call: bool = False,
                      extra_hints: str = "") -> Response:
    """Emit one <Play> per sentence clip, all nested inside a single barge-in
    <Gather> (so the caller can interrupt any sentence), then the listen fallbacks."""
    response = VoiceResponse()
    base_url = _base_url(request)
    if end_call:
        for fn in clips:
            response.play(f"{base_url}/audio/{fn}")
        response.hangup()
        return Response(content=str(response), media_type="application/xml")

    gather = build_gather(extra_hints)
    # Honor BARGE_IN: nest clips INSIDE the gather (interruptible) when on; play them
    # BEFORE the gather (recognizer listens only after the agent finishes — no echo
    # captured) when off. Previously clips were always nested, so BARGE_IN=0 had no
    # effect on the main streamed turns.
    target = gather if BARGE_IN else response
    for fn in clips:
        target.play(f"{base_url}/audio/{fn}")
    _append_listen_with_fallbacks(response, request, gather, extra_hints)
    return Response(content=str(response), media_type="application/xml")


def _twiml_trouble(request: Request, extra_hints: str = "", end_call: bool = False) -> Response:
    """Graceful degradation when Claude/Sarvam stalls or fails: play a short
    'bear with me' clip (pre-rendered, or Polly as a last resort) and keep the
    call alive by listening again instead of dropping into silence."""
    response = VoiceResponse()
    base_url = _base_url(request)

    def _emit(target):
        if FALLBACK_TROUBLE:
            target.play(f"{base_url}/audio/{FALLBACK_TROUBLE}")
        else:
            target.say(TROUBLE_TEXT, voice="Polly.Aditi")

    if end_call:
        _emit(response)
        response.hangup()
        return Response(content=str(response), media_type="application/xml")

    gather = build_gather(extra_hints)
    _emit(gather)
    _append_listen_with_fallbacks(response, request, gather, extra_hints)
    return Response(content=str(response), media_type="application/xml")


async def _run_stage(request: Request, user_speech: str, call_sid: str, state: dict,
                     history: list, stage: int) -> Response:
    """Single LLM turn: stream Claude, synthesize each sentence as it arrives, parse
    control tags to advance the state machine, and return a barge-in TwiML reply.
    On timeout/error the call degrades gracefully instead of dropping."""
    state["turn_count"] = state.get("turn_count", 0) + 1
    state["stage_turns"] = state.get("stage_turns", 0) + 1
    model = COMPLEX_MODEL if is_complex(user_speech, history) else ROUTINE_MODEL
    system_prompt = get_system_prompt(state["scenario"], stage, state.get("user_context", ""))
    pace = get_pace(stage, is_distressed(user_speech))
    extra_hints = state.get("extra_hints", "")

    try:
        full_text, clips = await _stream_and_synthesize(
            model, system_prompt, history, pace, state["metrics"])
    except Exception as e:
        print(f"[{call_sid}] LLM/TTS turn failed: {e}")
        if history and history[-1]["role"] == "user":
            history.pop()                     # let the caller retry this turn cleanly
        return _twiml_trouble(request, extra_hints)

    end_call = "[END_CALL]" in full_text
    if not end_call and "[NEXT_STAGE]" in full_text:
        state["stage"] = min(stage + 1, 4)
        state["stage_turns"] = 0
        print(f"[{call_sid}] Advanced to stage {state['stage']}")
    elif not end_call and state["stage_turns"] >= MAX_STAGE_TURNS:
        # SAFETY NET: the stage looped without emitting [NEXT_STAGE]. Don't get stuck.
        if stage < 4:
            state["stage"] = stage + 1
            state["stage_turns"] = 0
            print(f"[{call_sid}] SAFETY NET: force-advanced stage {stage}->{state['stage']} "
                  f"after {MAX_STAGE_TURNS} turns with no progress")
        else:
            end_call = True
            print(f"[{call_sid}] SAFETY NET: ending call — {MAX_STAGE_TURNS} turns stuck in final stage")
    if not end_call and state["turn_count"] >= MAX_CALL_TURNS:
        end_call = True
        print(f"[{call_sid}] SAFETY NET: hard cap of {MAX_CALL_TURNS} turns reached — ending call")

    clean_text = _clean_for_tts(full_text)
    m = state["metrics"]
    print(f"[{call_sid}] AI Response (stage {stage}): {clean_text}")
    print(f"[{call_sid}] [LATENCY] LLM {m['llm_latency']:.2f}s | TTS-tail {m['tts_latency']:.2f}s | "
          f"clips={len(clips)} | hist {m.get('history_sent', '?')}/{m.get('history_total', '?')} sent | "
          f"cache read={m.get('cache_read', 0)} created={m.get('cache_created', 0)}")
    history.append({"role": "assistant", "content": [{"type": "text", "text": clean_text}]})

    if not clips:                              # TTS produced nothing — don't play silence
        return _twiml_trouble(request, extra_hints, end_call=end_call)
    return _twiml_play_clips(request, clips, end_call=end_call, extra_hints=extra_hints)


async def _try_fast_path(request: Request, user_speech: str, call_sid: str, state: dict,
                         history: list) -> Response | None:
    """RAG FAST-PATH: on a confident intent match, reply with a clip pre-rendered at
    startup — zero Claude, zero TTS. Returns a Response on a hit, else None so the
    caller falls through to the LLM."""
    stage = state["stage"]
    inst = match_intent(user_speech, state["scenario"], stage,
                        fallback_scenario=CAMPAIGN_SCENARIO)
    if not inst or inst["id"] not in PRERENDERED_INTENTS:
        return None

    clean, tag = split_control_tag(inst.get("response", ""))
    print(f"[{call_sid}] FAST-PATH hit intent={inst['id']} tag={tag} (no LLM/TTS)")

    # Record both turns so the conversation stays coherent if it continues on the LLM path.
    history.append({"role": "user", "content": user_speech})
    history.append({"role": "assistant", "content": [{"type": "text", "text": clean}]})

    end_call = tag == "[END_CALL]"
    if tag == "[NEXT_STAGE]":
        state["stage"] = min(stage + 1, 4)
        state["stage_turns"] = 0
        print(f"[{call_sid}] Advanced to stage {state['stage']}")

    return Response(
        content=str(await gather_response(
            request, play_file=PRERENDERED_INTENTS[inst["id"]], end_call=end_call,
            extra_hints=state.get("extra_hints", ""))),
        media_type="application/xml",
    )


def _latency_line(call_sid: str, total_time: float, llm: float = 0.0, tts: float = 0.0,
                  label: str = "turn") -> str:
    """Per-turn perceived-latency breakdown. Separates the parts of the delay a caller
    feels: the endpointing wait (Twilio waits SPEECH_TIMEOUT of silence BEFORE the
    request reaches us — invisible to the server but real to the caller), the server
    work (LLM + the TTS not overlapped by streaming), and the rough total. Audio also
    only plays after the whole TwiML returns, so server-time ≈ added dead air."""
    if SPEECH_TIMEOUT_S is not None:
        endpoint = f"{SPEECH_TIMEOUT_S:.0f}s"
        perceived = f"{SPEECH_TIMEOUT_S + total_time:.2f}s"
    else:
        endpoint = str(SPEECH_TIMEOUT)
        perceived = "?"
    return (f"[{call_sid}] [LATENCY] {label}: endpoint~{endpoint} (pre-server, Twilio waits "
            f"for silence) | server {total_time:.2f}s (LLM {llm:.2f}s + TTS-tail {tts:.2f}s) "
            f"| ~caller-perceived {perceived} + network")


LATENCY_CSV = "latency_logs.csv"
# Each row separates the TWO actions a turn needs, so the log tells you WHICH one failed:
#   HEARING (did the agent capture your speech?):
#     Heard        = yes / no   (no = empty/garbled SpeechResult: noise, barge-in, silence)
#     Confidence   = Twilio's 0.0-1.0 transcription score  (low = shaky capture)
#     Transcript   = what was actually recognized  (blank/garbled = a hearing failure)
#   RESPONDING (how long to generate + speak the reply):
#     LLM_sec      = Claude generation
#     TTS_tail_sec = TTS time NOT overlapped by streaming
#     Server_sec   = our total processing (request in -> TwiML out); ~= added dead air
#   TURN-TAKING / TOTAL:
#     Endpoint_sec  = silence Twilio waits (SPEECH_TIMEOUT) BEFORE the request reaches us
#     Perceived_sec = Endpoint + Server (closest computable to what the caller feels; the
#                     residual on top is network/tunnel, which a server cannot measure).
# READING IT: many Heard=no / low Confidence -> a HEARING problem (try BARGE_IN=0, mic);
# Heard=yes but big LLM/TTS/Server -> a RESPONDING problem; both fine but caller still
# lags -> it's the Endpoint wait or the network.
LATENCY_HEADER = ["Timestamp", "Call_SID", "Stage", "Path",
                  "Heard", "Confidence", "Transcript",
                  "Endpoint_sec", "LLM_sec", "TTS_tail_sec", "Server_sec", "Perceived_sec"]


def _log_latency(call_sid: str, stage: int, llm: float, tts: float, total: float,
                 path: str = "llm", heard: str = "yes", confidence: str = "",
                 transcript: str = ""):
    try:
        endpoint = SPEECH_TIMEOUT_S            # None when SPEECH_TIMEOUT='auto'
        perceived = (endpoint + total) if endpoint is not None else None
        # If a CSV with a different (older) header exists, archive it so columns don't
        # misalign with the current schema.
        if os.path.exists(LATENCY_CSV):
            with open(LATENCY_CSV, "r", newline="") as f:
                if f.readline().strip() != ",".join(LATENCY_HEADER):
                    os.replace(LATENCY_CSV, "latency_logs.old.csv")
        file_exists = os.path.exists(LATENCY_CSV)
        with open(LATENCY_CSV, "a", newline="") as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(LATENCY_HEADER)
            writer.writerow([
                time.strftime("%Y-%m-%d %H:%M:%S"), call_sid, stage, path,
                heard, confidence or "", (transcript or "").strip()[:200],
                f"{endpoint:.2f}" if endpoint is not None else "auto",
                f"{llm:.2f}", f"{tts:.2f}", f"{total:.2f}",
                f"{perceived:.2f}" if perceived is not None else "",
            ])
    except Exception as e:
        print(f"Failed to write metrics to CSV: {e}")


@app.post("/process_speech")
async def process_speech(request: Request):
    req_start = time.time()
    form_data = await request.form()
    user_speech = form_data.get("SpeechResult", "")
    call_sid = form_data.get("CallSid", "")
    confidence = form_data.get("Confidence", "")
    # Raw STT diagnostics — fires on EVERY turn (even empty) so empty / garbled / low-
    # confidence transcripts can be correlated with barge-in firing. repr() exposes
    # whitespace and garbage; Confidence is Twilio's 0.0-1.0 transcription score (blank
    # when nothing was recognized).
    print(f"[{call_sid}] STT SpeechResult={user_speech!r} | Confidence={confidence or 'n/a'}")

    state = conversations.setdefault(
        call_sid,
        {"history": [], "scenario": "general", "stage": 1,
         "metrics": {"llm_latency": 0.0, "tts_latency": 0.0}},
    )
    state["metrics"] = {"llm_latency": 0.0, "tts_latency": 0.0}
    history = state["history"]

    if not user_speech:
        total_time = time.time() - req_start
        state["empty_count"] = state.get("empty_count", 0) + 1
        print(f"[{call_sid}] Empty SpeechResult from Twilio (Confidence={confidence or 'n/a'}) — "
              f"no speech captured ({state['empty_count']} in a row). Likely mic/audio path or silence.")
        # Log the HEARING FAILURE so it's visible in the sheet (this turn used to be
        # dropped from the CSV — masking exactly the "agent can't hear me" problem).
        _log_latency(call_sid, state.get("stage", 1), 0.0, 0.0, total_time,
                     path="reprompt", heard="no", confidence=confidence, transcript=user_speech)
        # Guard: actionOnEmptyResult makes a dead/silent line loop forever otherwise.
        if state["empty_count"] >= MAX_EMPTY_TURNS:
            print(f"[{call_sid}] {state['empty_count']} empty turns in a row — ending call gracefully.")
            return Response(content=str(await gather_response(
                request, say_text="I'm having trouble hearing you. I'll call you back later. Goodbye.",
                end_call=True)), media_type="application/xml")
        return Response(content=str(await gather_response(
            request, say_text="I didn't catch that. Could you please repeat?",
            extra_hints=state.get("extra_hints", ""))), media_type="application/xml")

    # LOW-CONFIDENCE GUARD: Twilio returns garbage (often the agent's own echo or noise) with
    # low/zero confidence. Don't act on it — treat like a no-catch and re-prompt, and count it
    # toward the same hang-up guard so a persistently bad line ends gracefully.
    try:
        conf_val = float(confidence) if confidence not in ("", None) else 0.0
    except (TypeError, ValueError):
        conf_val = 0.0
    if CONFIDENCE_MIN > 0 and conf_val < CONFIDENCE_MIN:
        total_time = time.time() - req_start
        state["empty_count"] = state.get("empty_count", 0) + 1
        print(f"[{call_sid}] LOW-CONFIDENCE drop ({conf_val:.2f} < {CONFIDENCE_MIN}): {user_speech!r} "
              f"({state['empty_count']} unclear in a row).")
        _log_latency(call_sid, state.get("stage", 1), 0.0, 0.0, total_time,
                     path="lowconf", heard="low", confidence=confidence, transcript=user_speech)
        if state["empty_count"] >= MAX_EMPTY_TURNS:
            return Response(content=str(await gather_response(
                request, say_text="I'm having trouble hearing you clearly. I'll call you back later. Goodbye.",
                end_call=True)), media_type="application/xml")
        return Response(content=str(await gather_response(
            request, say_text="Sorry, I didn't catch that clearly. Could you say it once more?",
            extra_hints=state.get("extra_hints", ""))), media_type="application/xml")

    state["empty_count"] = 0  # reset the no-speech counter on a successful capture
    print(f"[{call_sid}] Transcribed: {user_speech} (confidence {conf_val:.2f})")

    # FAST-TRACK END CALL — if the AI asked "Anything else?" and the user says "No".
    clean_speech = re.sub(r'[^\w\s]', '', user_speech.lower().strip())
    end_keywords = {"no", "nope", "thats it", "nothing", "nothing else", "no thank you",
                    "no thanks", "all good", "im good", "no that is all", "no thats all",
                    "thats all"}
    if state["stage"] >= 3 and clean_speech in end_keywords:
        print(f"[{call_sid}] Fast-tracking end call because user said: {clean_speech}")
        return Response(content=str(await gather_response(
            request, say_text="Thank you for your time today. We will make sure this is taken care of. Take care.",
            end_call=True)), media_type="application/xml")

    # Resolve the effective scenario (sticky + campaign default) for knowledge lookup.
    resolve_scenario(state, user_speech)

    # RAG FAST-PATH: confident canned reply, zero LLM + zero TTS.
    fast = await _try_fast_path(request, user_speech, call_sid, state, history)
    if fast is not None:
        total_time = time.time() - req_start
        print(_latency_line(call_sid, total_time, label="fast-path"))
        _log_latency(call_sid, state["stage"], 0.0, 0.0, total_time, path="fast-path",
                     heard="yes", confidence=confidence, transcript=user_speech)
        return fast

    # LLM PATH.
    history.append({"role": "user", "content": user_speech})
    stage = state["stage"]
    if stage in (1, 2, 3, 4):
        resp = await _run_stage(request, user_speech, call_sid, state, history, stage)
    else:
        resp = handle_error(call_sid, history, Exception("Invalid stage state"))

    total_time = time.time() - req_start
    print(_latency_line(call_sid, total_time,
                        state["metrics"]["llm_latency"], state["metrics"]["tts_latency"],
                        label=f"stage {state['stage']}"))
    _log_latency(call_sid, state["stage"],
                 state["metrics"]["llm_latency"], state["metrics"]["tts_latency"], total_time,
                 path="llm", heard="yes", confidence=confidence, transcript=user_speech)
    return resp


@app.post("/call_status")
async def call_status(request: Request):
    form_data = await request.form()
    call_sid = form_data.get("CallSid", "")
    call_status_val = form_data.get("CallStatus", "")
    if call_status_val in ("completed", "failed", "busy", "no-answer", "canceled"):
        conversations.pop(call_sid, None)
        print(f"[{call_sid}] Call ended ({call_status_val}); cleared memory.")
    return Response(status_code=204)


@app.post("/recording_status")
async def recording_status(request: Request, background_tasks: BackgroundTasks):
    form_data = await request.form()
    recording_url = form_data.get("RecordingUrl", "")
    recording_sid = form_data.get("RecordingSid", "")
    to_number = form_data.get("To") or form_data.get("Called") or ""

    if recording_url and recording_sid:
        print(f"New recording ready: {recording_sid}. Downloading...")

        def download_recording():
            try:
                import requests
                import os
                import time
                import re

                account_sid = os.environ.get("TWILIO_ACCOUNT_SID")
                auth_token = os.environ.get("TWILIO_AUTH_TOKEN")
                response = requests.get(f"{recording_url}.mp3", auth=(account_sid, auth_token))

                if response.status_code == 200:
                    clean_number = _phone_digits(to_number)
                    owner_name = "Unknown"
                    if clean_number:
                        context = get_user_context(clean_number)
                        if context:
                            for line in context.split("\n"):
                                if line.startswith("- Owner name:"):
                                    owner_name = line.split(":", 1)[1].strip()
                                    owner_name = re.sub(r'[^a-zA-Z0-9]', '_', owner_name)
                                    break

                    timestamp = time.strftime("%Y%m%d_%H%M%S")
                    subfolder_name = f"{clean_number}_{owner_name}"
                    subfolder_path = os.path.join("recordings", subfolder_name)
                    os.makedirs(subfolder_path, exist_ok=True)

                    filename = f"{timestamp}.mp3"
                    filepath = os.path.join(subfolder_path, filename)

                    with open(filepath, "wb") as f:
                        f.write(response.content)
                    print(f"[{recording_sid}] Successfully saved as {filepath}")
                else:
                    print(f"[{recording_sid}] Failed to download. Status: {response.status_code}")
            except Exception as e:
                print(f"[{recording_sid}] Download error: {e}")

        background_tasks.add_task(download_recording)

    return Response(status_code=204)


@app.get("/")
async def serve_index():
    with open("static/index.html", "r") as f:
        return HTMLResponse(content=f.read())


@app.get("/token")
async def get_token():
    account_sid = os.environ.get("TWILIO_ACCOUNT_SID")
    api_key = os.environ.get("TWILIO_API_KEY")
    api_secret = os.environ.get("TWILIO_API_SECRET")
    twiml_app_sid = os.environ.get("TWILIO_TWIML_APP_SID")

    if not all([account_sid, api_key, api_secret, twiml_app_sid]):
        return JSONResponse({"error": "Missing Twilio .env variables"}, status_code=500)

    token = AccessToken(account_sid, api_key, api_secret, identity="browser_user")
    voice_grant = VoiceGrant(outgoing_application_sid=twiml_app_sid, incoming_allow=True)
    token.add_grant(voice_grant)
    return JSONResponse({"token": token.to_jwt()})


_twilio_rest = None


def _twilio_client():
    """Lazy Twilio REST client (used to start recordings on in-progress calls)."""
    global _twilio_rest
    if _twilio_rest is None:
        sid = os.environ.get("TWILIO_ACCOUNT_SID")
        tok = os.environ.get("TWILIO_AUTH_TOKEN")
        if sid and tok:
            _twilio_rest = Client(sid, tok)
    return _twilio_rest


def _start_call_recording_sync(call_sid: str, base_url: str):
    """Start recording an in-progress call. Twilio POSTs to /recording_status when done,
    where the existing handler downloads the mp3 into recordings/."""
    try:
        client = _twilio_client()
        if not client or not call_sid:
            return
        client.calls(call_sid).recordings.create(
            recording_status_callback=f"{base_url}/recording_status",
            recording_status_callback_event=["completed"],
        )
        print(f"[{call_sid}] Recording started for browser call.")
    except Exception as e:
        print(f"[{call_sid}] Could not start recording: {e}")


@app.post("/voice")
async def voice(request: Request):
    """Browser test-client entrypoint (static/index.html). Loads the grooming test record
    and (optionally) records the call so it can be reviewed afterwards."""
    form_data = await request.form()
    call_sid = form_data.get("CallSid", "")
    _init_conversation(call_sid, TEST_CALL_IDENTIFIER)
    if RECORD_BROWSER_CALLS and call_sid:
        # Fire-and-forget so it never delays the greeting.
        asyncio.create_task(asyncio.to_thread(_start_call_recording_sync, call_sid, _base_url(request)))
    return await _build_greeting_twiml(request, call_sid)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
