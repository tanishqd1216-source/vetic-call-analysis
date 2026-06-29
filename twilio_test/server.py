from fastapi import FastAPI, Form, Request, BackgroundTasks
from fastapi.responses import Response, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from twilio.jwt.access_token import AccessToken
from twilio.jwt.access_token.grants import VoiceGrant
from twilio.twiml.voice_response import VoiceResponse, Gather, Play
from anthropic import AsyncAnthropic
from dotenv import load_dotenv
import psycopg2
import psycopg2.extras
import asyncio
import os
import re
import pandas as pd
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

# --- Load Excel Database ---
EXCEL_PATH = r"c:\Users\hp\Desktop\sample data\sample_ticket_summary.xlsx"
try:
    TICKET_DB = pd.read_excel(EXCEL_PATH)
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
            if len(target) == 10 and "mobile No." in TICKET_DB.columns:
                hits = TICKET_DB[TICKET_DB["mobile No."].map(_phone_digits) == target]
                if not hits.empty:
                    row = hits.iloc[0]
            if row is None:
                for col in ("Ticket ID", "user_ids"):
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
            ("Owner name", g("Full name")),
            ("Pet name", g("Pet name")),
            ("Service / appointment type", g("Final App type", "Appt_type", "Type")),
            ("Appointment date", g("appointment_date")),
            ("Clinic", g("final clinic Name")),
            ("City", g("City")),
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


async def _stream_and_synthesize(model: str, system_prompt: str, history: list,
                                 pace: float, metrics: dict):
    """Stream Claude; as each complete sentence arrives, fire its TTS concurrently so
    synthesis OVERLAPS generation instead of starting after it. Returns
    (full_text, [wav filenames, in spoken order])."""
    tts_tasks = []
    buf, full = "", ""
    llm_start = time.time()
    try:
        async with aclient.messages.stream(
            model=model, max_tokens=300, system=system_prompt, messages=history,
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
Maintain this warm and empathetic tone throughout the entire conversation.
The customer is likely unhappy or distressed. DO NOT sound joyful, cheerful, or overly enthusiastic. Maintain a calm, serious, and supportive tone throughout the entire conversation.
Your responses will be spoken aloud to a customer on a phone call using an Indian voice.
Keep answers brief and conversational.

CRITICAL RULES:
- Speak ONLY in English. Do not use Hindi words or script.
- Do not use filler sounds like "Umm", "Uhh", "Ah", or "Aaa". They cause the voice engine to stutter.
- Never exceed 3 sentences per response.
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
    """Single source of truth for every speech <Gather>. bargeIn=True lets the caller
    interrupt the nested audio; enhanced=True uses Google's telephony-tuned model."""
    hints = BASE_HINTS + (", " + extra_hints if extra_hints else "")
    return Gather(
        input="speech",
        action="/process_speech",
        timeout=15,
        speechTimeout="auto",
        bargeIn=True,
        enhanced=True,
        language="en-IN",
        hints=hints,
    )


def _append_listen_with_fallbacks(response: VoiceResponse, request: Request,
                                  gather: Gather, extra_hints: str = ""):
    """Append the main Gather (caller's turn), then a re-prompt Gather and a final
    goodbye — both PRE-RENDERED at startup. Speech audio is nested inside each Gather
    so the caller can barge in."""
    base_url = _base_url(request)
    response.append(gather)

    gather2 = build_gather(extra_hints)
    if FALLBACK_STILL_THERE:
        gather2.play(f"{base_url}/audio/{FALLBACK_STILL_THERE}")
    else:
        gather2.say(STILL_THERE_TEXT, voice="Polly.Aditi")
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

    # Main turn: audio nested INSIDE the Gather + bargeIn so the caller can interrupt.
    gather = build_gather(extra_hints)
    if play_file:
        gather.play(f"{base_url}/audio/{play_file}")
    elif say_text:
        gather.say(say_text, voice="Polly.Aditi")

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
    if greeting_file:
        gather.play(f"{base_url}/audio/{greeting_file}")
    else:
        gather.say(greeting, voice="Polly.Aditi")

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
    _init_conversation(call_sid, to_number or "8273004988")
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
    for fn in clips:
        gather.play(f"{base_url}/audio/{fn}")
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
        print(f"[{call_sid}] Advanced to stage {state['stage']}")

    clean_text = _clean_for_tts(full_text)
    print(f"[{call_sid}] AI Response (stage {stage}): {clean_text}")
    print(f"[{call_sid}] [LATENCY] LLM {state['metrics']['llm_latency']:.2f}s | "
          f"TTS-tail {state['metrics']['tts_latency']:.2f}s | clips={len(clips)}")
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
        print(f"[{call_sid}] Advanced to stage {state['stage']}")

    return Response(
        content=str(await gather_response(
            request, play_file=PRERENDERED_INTENTS[inst["id"]], end_call=end_call,
            extra_hints=state.get("extra_hints", ""))),
        media_type="application/xml",
    )


def _log_latency(call_sid: str, stage: int, llm: float, tts: float, total: float):
    try:
        csv_path = "latency_logs.csv"
        file_exists = os.path.exists(csv_path)
        with open(csv_path, "a", newline="") as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(["Timestamp", "Call_SID", "Stage", "LLM_Latency_sec",
                                 "TTS_Latency_sec", "Total_Latency_sec"])
            writer.writerow([time.strftime("%Y-%m-%d %H:%M:%S"), call_sid, stage,
                             f"{llm:.2f}", f"{tts:.2f}", f"{total:.2f}"])
    except Exception as e:
        print(f"Failed to write metrics to CSV: {e}")


@app.post("/process_speech")
async def process_speech(request: Request):
    req_start = time.time()
    form_data = await request.form()
    user_speech = form_data.get("SpeechResult", "")
    call_sid = form_data.get("CallSid", "")

    state = conversations.setdefault(
        call_sid,
        {"history": [], "scenario": "general", "stage": 1,
         "metrics": {"llm_latency": 0.0, "tts_latency": 0.0}},
    )
    state["metrics"] = {"llm_latency": 0.0, "tts_latency": 0.0}
    history = state["history"]

    if not user_speech:
        print(f"[{call_sid}] Received empty SpeechResult from Twilio. Prompting to repeat.")
        return Response(content=str(await gather_response(
            request, say_text="I didn't catch that. Could you please repeat?",
            extra_hints=state.get("extra_hints", ""))), media_type="application/xml")

    print(f"[{call_sid}] Transcribed: {user_speech}")

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
        print(f"[{call_sid}] [LATENCY] Fast-path turn took {total_time:.2f}s")
        _log_latency(call_sid, state["stage"], 0.0, 0.0, total_time)
        return fast

    # LLM PATH.
    history.append({"role": "user", "content": user_speech})
    stage = state["stage"]
    if stage in (1, 2, 3, 4):
        resp = await _run_stage(request, user_speech, call_sid, state, history, stage)
    else:
        resp = handle_error(call_sid, history, Exception("Invalid stage state"))

    total_time = time.time() - req_start
    print(f"[{call_sid}] [LATENCY] Total request processing took {total_time:.2f}s")
    _log_latency(call_sid, state["stage"],
                 state["metrics"]["llm_latency"], state["metrics"]["tts_latency"], total_time)
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


@app.post("/voice")
async def voice(request: Request):
    """Browser test-client entrypoint (static/index.html). Uses the fixed test number."""
    form_data = await request.form()
    call_sid = form_data.get("CallSid", "")
    _init_conversation(call_sid, "8273004988")
    return await _build_greeting_twiml(request, call_sid)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
