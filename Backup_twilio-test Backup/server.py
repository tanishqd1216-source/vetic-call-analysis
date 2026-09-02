from fastapi import FastAPI, Form, Request
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from twilio.twiml.voice_response import VoiceResponse, Gather, Play
from anthropic import Anthropic
from dotenv import load_dotenv
import psycopg2
import psycopg2.extras
import os
import re

load_dotenv()

app = FastAPI()

os.makedirs("audio", exist_ok=True)
app.mount("/audio", StaticFiles(directory="audio"), name="audio")

client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
DATABASE_URL = os.environ.get("DATABASE_URL")
SARVAM_API_KEY = os.environ.get("SARVAM_API_KEY")

def generate_sarvam_tts(text: str) -> str:
    print("Generating TTS with Sarvam AI...")
    import requests, uuid
    url = "https://api.sarvam.ai/text-to-speech"
    headers = {"api-subscription-key": SARVAM_API_KEY, "Content-Type": "application/json"}
    payload = {"inputs": [text], "target_language_code": "hi-IN", "speaker": "meera", "pitch": 0, "pace": 1.05, "loudness": 1.5, "speech_sample_rate": 8000, "enable_preprocessing": True, "model": "bulbul:v1"}
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

ROUTINE_MODEL = "claude-3-haiku-20240307"
COMPLEX_MODEL = "claude-3-haiku-20240307"

BASE_RULES = """
You are Aarya, a warm, empathetic AI voice assistant for Vetic Pet Care.
Your responses will be spoken aloud to a customer on a phone call using an Indian voice.
Keep answers brief and conversational — NO bullet points, bold, or markdown.

CRITICAL RULES:
- Never exceed 3 sentences per response.
- Do not list options like a robot; speak naturally.
- Only answer what was asked. Wait for the user to respond before offering more info.
"""

def get_system_prompt(scenario: str, stage: int) -> str:
    prompt_file = ""
    if stage == 1: prompt_file = "prompts/1_introduction.txt"
    elif stage == 2: prompt_file = "prompts/2_context.txt"
    elif stage == 3: prompt_file = "prompts/3_resolution.txt"
    elif stage == 4: prompt_file = "prompts/4_conclusion.txt"
    
    with open(prompt_file, "r") as f:
        stage_rules = f.read()
        
    prompt = BASE_RULES + "\n\n" + stage_rules
    return prompt

conversations: dict[str, dict] = {}

def gather_response(request: Request, say_text: str = None, play_file: str = None, end_call: bool = False) -> VoiceResponse:
    response = VoiceResponse()
    
    if say_text and not play_file:
        if SARVAM_API_KEY:
            play_file = generate_sarvam_tts(say_text)

    if end_call:
        if play_file:
            base_url = str(request.base_url).rstrip("/")
            audio_url = f"{base_url}/audio/{play_file}"
            response.play(audio_url)
        else:
            response.say(say_text, voice="Polly.Aditi")
        response.hangup()
        return response
            
    gather = Gather(
        input="speech",
        action="/process_speech",
        timeout=10,
        speechTimeout="auto",
        bargeIn=True,
        language="en-IN"
    )
    
    if play_file:
        base_url = str(request.base_url).rstrip("/")
        audio_url = f"{base_url}/audio/{play_file}"
        gather.play(audio_url)
    elif say_text:
        gather.say(say_text, voice="Polly.Aditi")

    response.append(gather)
    
    gather2 = Gather(
        input="speech", action="/process_speech",
        timeout=10, speechTimeout="auto", bargeIn=True, language="en-IN"
    )
    gather2.say("Are you still there? Please let me know if you need help.", voice="Polly.Aditi")
    response.append(gather2)
    
    response.say("I didn't hear anything. Goodbye!", voice="Polly.Aditi")
            
    response.hangup()
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
    conversations[call_sid] = {"history": [], "scenario": "general", "stage": 1}
    
    greeting = "Good morning, this is Aarya calling from Vetic. Can we have a quick call?"
    response = VoiceResponse()
    response.pause(length=1)
    
    if SARVAM_API_KEY:
        play_file = generate_sarvam_tts(greeting)
        if play_file:
            base_url = str(request.base_url).rstrip("/")
            audio_url = f"{base_url}/audio/{play_file}"
            response.play(audio_url)
    else:
        response.say(greeting, voice="Polly.Aditi")
    
    gather = Gather(input="speech", action="/process_speech", timeout=10, speechTimeout="auto", bargeIn=True, language="en-IN")
    response.append(gather)
    return Response(content=str(response), media_type="application/xml")

async def handle_introduction(request: Request, user_speech: str, call_sid: str, state: dict, history: list) -> Response:
    model = COMPLEX_MODEL if is_complex(user_speech, history) else ROUTINE_MODEL
    system_prompt = get_system_prompt(state["scenario"], 1)
    try:
        message = client.messages.create(model=model, max_tokens=300, system=system_prompt, messages=history)
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
        return Response(content=str(gather_response(request, say_text=ai_response, end_call=end_call)), media_type="application/xml")
    except Exception as e:
        return handle_error(call_sid, history, e)

async def handle_context(request: Request, user_speech: str, call_sid: str, state: dict, history: list) -> Response:
    model = COMPLEX_MODEL if is_complex(user_speech, history) else ROUTINE_MODEL
    system_prompt = get_system_prompt(state["scenario"], 2)
    try:
        message = client.messages.create(model=model, max_tokens=300, system=system_prompt, messages=history)
        ai_response = "".join(block.text for block in message.content if block.type == "text")
        if "[NEXT_STAGE]" in ai_response:
            ai_response = ai_response.replace("[NEXT_STAGE]", "").strip()
            state["stage"] = 3
            print(f"[{call_sid}] Advanced to stage 3: RESOLUTION")
        print(f"[{call_sid}] AI Response (Context): {ai_response}")
        history.append({"role": "assistant", "content": [{"type": "text", "text": ai_response}]})
        return Response(content=str(gather_response(request, say_text=ai_response)), media_type="application/xml")
    except Exception as e:
        return handle_error(call_sid, history, e)

async def handle_resolution(request: Request, user_speech: str, call_sid: str, state: dict, history: list) -> Response:
    model = COMPLEX_MODEL if is_complex(user_speech, history) else ROUTINE_MODEL
    system_prompt = get_system_prompt(state["scenario"], 3)
    if state["scenario"] == "opd":
        with open("prompts/sop_opd.txt", "r") as f: system_prompt += "\n\n" + f.read()
    elif state["scenario"] == "grooming":
        with open("prompts/sop_grooming.txt", "r") as f: system_prompt += "\n\n" + f.read()
    try:
        message = client.messages.create(model=model, max_tokens=300, system=system_prompt, messages=history)
        ai_response = "".join(block.text for block in message.content if block.type == "text")
        if "[NEXT_STAGE]" in ai_response:
            ai_response = ai_response.replace("[NEXT_STAGE]", "").strip()
            state["stage"] = 4
            print(f"[{call_sid}] Advanced to stage 4: CONCLUSION")
        print(f"[{call_sid}] AI Response (Resolution): {ai_response}")
        history.append({"role": "assistant", "content": [{"type": "text", "text": ai_response}]})
        return Response(content=str(gather_response(request, say_text=ai_response)), media_type="application/xml")
    except Exception as e:
        return handle_error(call_sid, history, e)

async def handle_conclusion(request: Request, user_speech: str, call_sid: str, state: dict, history: list) -> Response:
    model = COMPLEX_MODEL if is_complex(user_speech, history) else ROUTINE_MODEL
    system_prompt = get_system_prompt(state["scenario"], 4)
    try:
        message = client.messages.create(model=model, max_tokens=300, system=system_prompt, messages=history)
        ai_response = "".join(block.text for block in message.content if block.type == "text")
        end_call = False
        if "[END_CALL]" in ai_response:
            ai_response = ai_response.replace("[END_CALL]", "").strip()
            end_call = True
        print(f"[{call_sid}] AI Response (Conclusion): {ai_response}")
        history.append({"role": "assistant", "content": [{"type": "text", "text": ai_response}]})
        return Response(content=str(gather_response(request, say_text=ai_response, end_call=end_call)), media_type="application/xml")
    except Exception as e:
        return handle_error(call_sid, history, e)

@app.post("/process_speech")
async def process_speech(request: Request):
    form_data = await request.form()
    user_speech = form_data.get("SpeechResult", "")
    call_sid = form_data.get("CallSid", "")

    state = conversations.setdefault(call_sid, {"history": [], "scenario": "general", "stage": 1})
    history = state["history"]

    if not user_speech:
        return Response(content=str(gather_response(request, say_text="I didn't catch that. Could you please repeat?")), media_type="application/xml")

    print(f"[{call_sid}] Transcribed: {user_speech}")

    clean_speech = re.sub(r'[^\w\s]', '', user_speech.lower().strip())
    words = clean_speech.split()
    
    is_negative = False
    if len(words) <= 7:
        if any(w in words for w in ["no", "nope", "nothing", "nah"]) or "thats it" in clean_speech or "all good" in clean_speech or "thats all" in clean_speech:
            is_negative = True

    if state["stage"] >= 3 and is_negative:
        print(f"[{call_sid}] Fast-tracking end call because user said: {user_speech}")
        return Response(content=str(gather_response(request, say_text="Thank you for choosing Vetic. Have a wonderful day!", end_call=True)), media_type="application/xml")

    scenario = detect_scenario(user_speech, history)
    state["scenario"] = scenario
    
    history.append({"role": "user", "content": user_speech})
    
    stage = state["stage"]
    if stage == 1: return await handle_introduction(request, user_speech, call_sid, state, history)
    elif stage == 2: return await handle_context(request, user_speech, call_sid, state, history)
    elif stage == 3: return await handle_resolution(request, user_speech, call_sid, state, history)
    elif stage == 4: return await handle_conclusion(request, user_speech, call_sid, state, history)
    else: return handle_error(call_sid, history, Exception("Invalid stage state"))

@app.post("/call_status")
async def call_status(request: Request):
    form_data = await request.form()
    call_sid = form_data.get("CallSid", "")
    call_status_val = form_data.get("CallStatus", "")
    if call_status_val in ("completed", "failed", "busy", "no-answer", "canceled"):
        conversations.pop(call_sid, None)
        print(f"[{call_sid}] Call ended ({call_status_val}); cleared memory.")
    return Response(status_code=204)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
