from fastapi import FastAPI, Form, Request, BackgroundTasks
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from twilio.twiml.voice_response import VoiceResponse, Gather, Play
from anthropic import Anthropic
from dotenv import load_dotenv
import psycopg2
import psycopg2.extras
import os
import re
import pandas as pd
import time
import csv

# ---------------------------------------------------------------------------
# server_hybrid_prerender_bargein.py  —  best of both:
#   * BARGE-IN: bargeIn=True with audio nested INSIDE <Gather> (caller can interrupt).
#   * PRE-RENDER: the FIXED fallback phrases ("Are you still there?" / goodbye) are
#     synthesized via Sarvam ONCE at startup, not on every call. The greeting and the
#     AI replies are still synthesized per-call (they change every time).
# Everything else matches server_hybrid.py (latency logging, personalized greeting,
# empathy/anti-hallucination rules, prompts/gemini/ path, per-customer recordings).
# ---------------------------------------------------------------------------

load_dotenv()

app = FastAPI()

# Fixed fallback phrases — pre-rendered once at startup (see startup_event below).
STILL_THERE_TEXT = "Are you still there? Please let me know if you need help."
GOODBYE_TEXT = "I didn't hear anything. I will connect to you again after some time."
FALLBACK_STILL_THERE = None
FALLBACK_GOODBYE = None

@app.on_event("startup")
async def startup_event():
    global FALLBACK_STILL_THERE, FALLBACK_GOODBYE
    FALLBACK_STILL_THERE = generate_sarvam_tts(STILL_THERE_TEXT)
    FALLBACK_GOODBYE = generate_sarvam_tts(GOODBYE_TEXT)


os.makedirs("audio", exist_ok=True)
app.mount("/audio", StaticFiles(directory="audio"), name="audio")

client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
DATABASE_URL = os.environ.get("DATABASE_URL")
SARVAM_API_KEY = os.environ.get("SARVAM_API_KEY")

HINTS = "Toto, Marshal, Vetic, grooming, vaccination, checkup, pet"

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


def generate_sarvam_tts(text: str) -> str:
    print("Generating TTS with Sarvam AI...")
    import requests, uuid
    url = "https://api.sarvam.ai/text-to-speech"
    headers = {"api-subscription-key": SARVAM_API_KEY, "Content-Type": "application/json"}
    payload = {"inputs": [text], "target_language_code": "en-IN", "speaker": "priya", "pace": 1.15, "speech_sample_rate": 8000, "enable_preprocessing": True, "model": "bulbul:v3"}
    response = requests.post(url, json=payload, headers=headers)
    if response.status_code == 200:
        audio_base64 = response.json().get("audios", [""])[0]
        if audio_base64:
            import base64
            audio_data = base64.b64decode(audio_base64)
            filename = f"{uuid.uuid4()}.wav"
            filepath = os.path.join("audio", filename)
            with open(filepath, "wb") as f:
                f.write(audio_data)
            return filename
    return None


def _base_url(request: Request) -> str:
    return request.headers.get("x-forwarded-proto", "http") + "://" + request.headers.get(
        "x-forwarded-host", request.headers.get("host", "127.0.0.1:8000")
    )


def _make_gather() -> Gather:
    """A speech Gather with barge-in enabled (caller can interrupt the nested audio)."""
    return Gather(
        input="speech",
        action="/process_speech",
        timeout=15,
        speechTimeout="auto",
        bargeIn=True,
        language="en-IN",
        enhanced=True,
        hints=HINTS,
    )


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

ROUTINE_MODEL = "claude-haiku-4-5-20251001"
COMPLEX_MODEL = "claude-haiku-4-5-20251001"

BASE_RULES = """
You are Aarya, a warm, empathetic, and welcoming AI customer support agent for Vetic Pet Care.
Maintain this warm and empathetic tone throughout the entire conversation.
Your responses will be spoken aloud to a customer on a phone call using an Indian voice.
Keep answers brief and conversational — NO bullet points, bold, or markdown.

CRITICAL RULES:
- Speak ONLY in English. Do not use Hindi words or script.
- Do not use filler sounds like "Umm", "Uhh", "Ah", or "Aaa". They cause the voice engine to stutter.
- Never exceed 3 sentences per response.
- IMPORTANT COMPLAINT HANDLING: The customer is likely calling because they are unhappy with the service they received. You must listen patiently, apologize sincerely on behalf of Vetic, and focus on de-escalating the situation. Assure them their feedback is taken seriously and offer a concrete resolution or escalation to management. Maintain your warm and empathetic tone while handling complaints.
- EMPATHY RULE: DO NOT ever use the word "appreciate" (e.g., "I appreciate your feedback", "I appreciate you telling me"). Instead, express genuine empathy using phrases like "I understand completely", "I'm so sorry you experienced that", or "That sounds really difficult".
- ANTI-HALLUCINATION RULE: Do not make up facts, procedures, policies, or solutions. Stick strictly to the context and flow provided. If you don't know something, do not guess; simply state that you will have a manager reach out to assist them.
- Do not list options like a robot; speak naturally.
- Only answer what was asked. Wait for the user to respond before offering more info.
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
    return prompt

conversations: dict[str, dict] = {}

def _append_listen_with_fallbacks(response: VoiceResponse, request: Request, gather: Gather):
    """Append the main Gather, then a re-prompt Gather and a final goodbye —
    both PRE-RENDERED once at startup. Speech audio is nested inside its Gather
    (barge-in), so the caller can interrupt."""
    base_url = _base_url(request)
    response.append(gather)

    # Re-prompt — PRE-RENDERED at startup, nested + bargeIn.
    gather2 = _make_gather()
    if FALLBACK_STILL_THERE:
        gather2.play(f"{base_url}/audio/{FALLBACK_STILL_THERE}")
    else:
        gather2.say(STILL_THERE_TEXT, voice="Polly.Aditi")
    response.append(gather2)

    # Final goodbye — PRE-RENDERED at startup.
    if FALLBACK_GOODBYE:
        response.play(f"{base_url}/audio/{FALLBACK_GOODBYE}")
    else:
        response.say(GOODBYE_TEXT, voice="Polly.Aditi")
    response.hangup()


def gather_response(request: Request, say_text: str = None, play_file: str = None, end_call: bool = False, metrics: dict = None) -> VoiceResponse:
    response = VoiceResponse()
    base_url = _base_url(request)

    if say_text and not play_file:
        if SARVAM_API_KEY:
            tts_start = time.time()
            play_file = generate_sarvam_tts(say_text)
            tts_time = time.time() - tts_start
            print(f"[LATENCY] TTS generated in {tts_time:.2f}s")
            if metrics is not None:
                metrics["tts_latency"] = tts_time

    if end_call:
        if play_file:
            response.play(f"{base_url}/audio/{play_file}")
        else:
            response.say(say_text, voice="Polly.Aditi")
        response.hangup()
        return response

    # Main turn: audio nested INSIDE the Gather + bargeIn so the caller can interrupt.
    gather = _make_gather()
    if play_file:
        gather.play(f"{base_url}/audio/{play_file}")
    elif say_text:
        gather.say(say_text, voice="Polly.Aditi")

    _append_listen_with_fallbacks(response, request, gather)
    return response

def handle_error(call_sid: str, history: list, e: Exception) -> Response:
    print(f"[{call_sid}] Error: {e}")
    if history and history[-1]["role"] == "user":
        history.pop()
    response = VoiceResponse()
    response.hangup()
    return Response(content=str(response), media_type="application/xml")

@app.post("/incoming_call")
async def incoming_call(request: Request):
    form_data = await request.form()
    call_sid = form_data.get("CallSid", "")
    base_url = _base_url(request)

    # Match the customer by the number we are calling ('To'); fall back to the test number.
    to_number = form_data.get("To") or form_data.get("Called") or ""
    user_context = get_user_context(to_number or "8273004988")

    conversations[call_sid] = {
        "history": [],
        "scenario": "general",
        "stage": 1,
        "user_context": user_context
    }

    owner_name = ""
    if user_context:
        for line in user_context.split("\n"):
            if line.startswith("- Owner name:"):
                owner_name = line.split(":", 1)[1].strip().split()[0]
                break

    greeting = "Good morning, this is Aarya calling from Vetic. Can we have a quick call?"

    response = VoiceResponse()
    response.pause(length=1)

    # Greeting (personalized -> synthesized per-call) nested INSIDE the Gather + bargeIn.
    greeting_file = generate_sarvam_tts(greeting) if SARVAM_API_KEY else None
    gather = _make_gather()
    if greeting_file:
        gather.play(f"{base_url}/audio/{greeting_file}")
    else:
        gather.say(greeting, voice="Polly.Aditi")

    _append_listen_with_fallbacks(response, request, gather)

    return Response(content=str(response), media_type="application/xml")

async def handle_introduction(request: Request, user_speech: str, call_sid: str, state: dict, history: list) -> Response:
    model = COMPLEX_MODEL if is_complex(user_speech, history) else ROUTINE_MODEL
    system_prompt = get_system_prompt(state["scenario"], 1, state.get("user_context", ""))
    try:
        llm_start = time.time()
        message = client.messages.create(model=model, max_tokens=300, system=system_prompt, messages=history)
        llm_time = time.time() - llm_start
        state["metrics"]["llm_latency"] = llm_time
        print(f"[{call_sid}] [LATENCY] LLM generation took {llm_time:.2f}s")
        ai_response = "".join(block.text for block in message.content if block.type == "text")
        end_call = False
        if "[END_CALL]" in ai_response:
            ai_response = ai_response.replace("[END_CALL]", "").strip()
            end_call = True
        elif "[NEXT_STAGE]" in ai_response:
            ai_response = ai_response.replace("[NEXT_STAGE]", "").strip()
            state["stage"] = 2
            print(f"[{call_sid}] Advanced to stage 2: CONTEXT")
        print(f"[{call_sid}] AI Response (Intro): {ai_response}")
        history.append({"role": "assistant", "content": [{"type": "text", "text": ai_response}]})
        return Response(content=str(gather_response(request, say_text=ai_response, end_call=end_call, metrics=state.get("metrics"))), media_type="application/xml")
    except Exception as e:
        return handle_error(call_sid, history, e)

async def handle_context(request: Request, user_speech: str, call_sid: str, state: dict, history: list) -> Response:
    model = COMPLEX_MODEL if is_complex(user_speech, history) else ROUTINE_MODEL
    system_prompt = get_system_prompt(state["scenario"], 2, state.get("user_context", ""))
    try:
        llm_start = time.time()
        message = client.messages.create(model=model, max_tokens=300, system=system_prompt, messages=history)
        llm_time = time.time() - llm_start
        state["metrics"]["llm_latency"] = llm_time
        print(f"[{call_sid}] [LATENCY] LLM generation took {llm_time:.2f}s")
        ai_response = "".join(block.text for block in message.content if block.type == "text")
        if "[NEXT_STAGE]" in ai_response:
            ai_response = ai_response.replace("[NEXT_STAGE]", "").strip()
            state["stage"] = 3
            print(f"[{call_sid}] Advanced to stage 3: RESOLUTION")
        print(f"[{call_sid}] AI Response (Context): {ai_response}")
        history.append({"role": "assistant", "content": [{"type": "text", "text": ai_response}]})
        return Response(content=str(gather_response(request, say_text=ai_response, metrics=state.get("metrics"))), media_type="application/xml")
    except Exception as e:
        return handle_error(call_sid, history, e)

async def handle_resolution(request: Request, user_speech: str, call_sid: str, state: dict, history: list) -> Response:
    model = COMPLEX_MODEL if is_complex(user_speech, history) else ROUTINE_MODEL
    system_prompt = get_system_prompt(state["scenario"], 3, state.get("user_context", ""))

    try:
        llm_start = time.time()
        message = client.messages.create(model=model, max_tokens=300, system=system_prompt, messages=history)
        llm_time = time.time() - llm_start
        state["metrics"]["llm_latency"] = llm_time
        print(f"[{call_sid}] [LATENCY] LLM generation took {llm_time:.2f}s")
        ai_response = "".join(block.text for block in message.content if block.type == "text")
        if "[NEXT_STAGE]" in ai_response:
            ai_response = ai_response.replace("[NEXT_STAGE]", "").strip()
            state["stage"] = 4
            print(f"[{call_sid}] Advanced to stage 4: CONCLUSION")
        print(f"[{call_sid}] AI Response (Resolution): {ai_response}")
        history.append({"role": "assistant", "content": [{"type": "text", "text": ai_response}]})
        return Response(content=str(gather_response(request, say_text=ai_response, metrics=state.get("metrics"))), media_type="application/xml")
    except Exception as e:
        return handle_error(call_sid, history, e)

async def handle_conclusion(request: Request, user_speech: str, call_sid: str, state: dict, history: list) -> Response:
    model = COMPLEX_MODEL if is_complex(user_speech, history) else ROUTINE_MODEL
    system_prompt = get_system_prompt(state["scenario"], 4, state.get("user_context", ""))
    try:
        llm_start = time.time()
        message = client.messages.create(model=model, max_tokens=300, system=system_prompt, messages=history)
        llm_time = time.time() - llm_start
        state["metrics"]["llm_latency"] = llm_time
        print(f"[{call_sid}] [LATENCY] LLM generation took {llm_time:.2f}s")
        ai_response = "".join(block.text for block in message.content if block.type == "text")
        end_call = False
        if "[END_CALL]" in ai_response:
            ai_response = ai_response.replace("[END_CALL]", "").strip()
            end_call = True
        print(f"[{call_sid}] AI Response (Conclusion): {ai_response}")
        history.append({"role": "assistant", "content": [{"type": "text", "text": ai_response}]})
        return Response(content=str(gather_response(request, say_text=ai_response, end_call=end_call, metrics=state.get("metrics"))), media_type="application/xml")
    except Exception as e:
        return handle_error(call_sid, history, e)

@app.post("/process_speech")
async def process_speech(request: Request):
    req_start = time.time()
    form_data = await request.form()
    user_speech = form_data.get("SpeechResult", "")
    call_sid = form_data.get("CallSid", "")

    state = conversations.setdefault(call_sid, {"history": [], "scenario": "general", "stage": 1, "metrics": {}})
    state["metrics"] = {"llm_latency": 0.0, "tts_latency": 0.0}
    history = state["history"]

    if not user_speech:
        print(f"[{call_sid}] Received empty SpeechResult from Twilio. Prompting to repeat.")
        return Response(content=str(gather_response(request, say_text="I didn't catch that. Could you please repeat?")), media_type="application/xml")

    print(f"[{call_sid}] Transcribed: {user_speech}")
    print(f"[{call_sid}] [LATENCY] Speech received. Processing started at {req_start:.2f}")

    # FAST-TRACK END CALL
    # If the AI asked "Anything else?" and the user says "No", hang up instantly.
    clean_speech = re.sub(r'[^\w\s]', '', user_speech.lower().strip())
    end_keywords = {"no", "nope", "thats it", "nothing", "nothing else", "no thank you", "no thanks", "all good", "im good", "no that is all", "no thats all", "thats all"}
    if state["stage"] >= 3 and clean_speech in end_keywords:
        print(f"[{call_sid}] Fast-tracking end call because user said: {clean_speech}")
        return Response(content=str(gather_response(request, say_text="Thank you for choosing Vetic. Have a wonderful day!", end_call=True)), media_type="application/xml")

    scenario = detect_scenario(user_speech, history)
    state["scenario"] = scenario

    history.append({"role": "user", "content": user_speech})

    stage = state["stage"]
    if stage == 1: resp = await handle_introduction(request, user_speech, call_sid, state, history)
    elif stage == 2: resp = await handle_context(request, user_speech, call_sid, state, history)
    elif stage == 3: resp = await handle_resolution(request, user_speech, call_sid, state, history)
    elif stage == 4: resp = await handle_conclusion(request, user_speech, call_sid, state, history)
    else: resp = handle_error(call_sid, history, Exception("Invalid stage state"))

    total_time = time.time() - req_start
    print(f"[{call_sid}] [LATENCY] Total request processing took {total_time:.2f}s")

    # Save latency metrics to CSV
    try:
        csv_path = "latency_logs.csv"
        file_exists = os.path.exists(csv_path)
        with open(csv_path, "a", newline="") as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(["Timestamp", "Call_SID", "Stage", "LLM_Latency_sec", "TTS_Latency_sec", "Total_Latency_sec"])
            writer.writerow([
                time.strftime("%Y-%m-%d %H:%M:%S"),
                call_sid,
                state["stage"],
                f"{state['metrics']['llm_latency']:.2f}",
                f"{state['metrics']['tts_latency']:.2f}",
                f"{total_time:.2f}"
            ])
    except Exception as e:
        print(f"Failed to write metrics to CSV: {e}")

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
                    # Determine owner name and phone digits for the tag
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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
