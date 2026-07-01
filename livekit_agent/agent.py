"""Vetic service-recovery voice agent on LiveKit — Sarvam STT + TTS, Claude LLM.

Pipeline:  caller (WebRTC/SIP) -> Sarvam STT (saaras:v3) -> Claude -> Sarvam TTS
           (bulbul:v3, "priya") -> caller.

Uses the official `livekit-plugins-sarvam` plugin for BOTH speech-to-text and
text-to-speech, so there are no hand-built adapters. Sarvam's STT handles voice
activity / endpointing internally (turn_detection="stt"), so no separate VAD is needed.

Ports the Twilio agent's brain (persona, call flow, DB personalization). The spoken
control-tag state machine ([NEXT_STAGE]/[END_CALL]) is replaced by one consolidated
prompt + an `end_call` function tool.
"""
import csv
import datetime
import logging
import os

from dotenv import load_dotenv
from livekit import api
from livekit.agents import (
    JobContext,
    MetricsCollectedEvent,
    RoomInputOptions,
    RunContext,
    WorkerOptions,
    cli,
    function_tool,
    get_job_context,
    metrics,
)
from livekit.agents.voice import Agent, AgentSession
from livekit.plugins import anthropic, noise_cancellation, sarvam

from vetic_db import get_user_context, owner_first_name

load_dotenv()
logger = logging.getLogger("vetic-agent")
logger.setLevel(logging.INFO)


def _env_bool(name, default=None):
    v = os.environ.get(name)
    if v is None or v.strip() == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _env_num(name, cast):
    v = os.environ.get(name)
    if v is None or v.strip() == "":
        return None
    try:
        return cast(v)
    except ValueError:
        return None


# ── Per-turn latency logging ──────────────────────────────────────────────────
# LiveKit emits separate metric events (EOU, LLM, TTS) per turn, correlated by
# speech_id. We assemble them into one row and write the perceived response latency
# (time from the caller stopping → Aarya starting to speak) to latency_logs.csv.
LATENCY_CSV = os.environ.get("LATENCY_CSV_PATH") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "latency_logs.csv")
LATENCY_HEADER = ["Timestamp", "Speech_ID", "EOU_delay_s", "Transcription_delay_s",
                  "LLM_TTFT_s", "TTS_TTFB_s", "Response_latency_s"]
_turns: dict = {}


def _write_latency_row(row) -> None:
    try:
        # If a CSV with a different schema sits at this path (e.g. the old Twilio
        # 12-column log), archive it so columns don't misalign, then start fresh.
        if os.path.exists(LATENCY_CSV):
            with open(LATENCY_CSV, "r", encoding="utf-8", errors="ignore") as f:
                first = f.readline().strip()
            if first and first != ",".join(LATENCY_HEADER):
                base = LATENCY_CSV[:-4] if LATENCY_CSV.lower().endswith(".csv") else LATENCY_CSV
                archive, n = base + ".prev.csv", 1
                while os.path.exists(archive):
                    n += 1
                    archive = base + f".prev{n}.csv"
                os.replace(LATENCY_CSV, archive)
                logger.info("archived prior latency log -> %s", archive)
        new = not os.path.exists(LATENCY_CSV)
        with open(LATENCY_CSV, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(LATENCY_HEADER)
            w.writerow(row)
    except Exception as e:
        logger.warning("latency csv write failed: %s", e)


def _on_metrics(ev: MetricsCollectedEvent) -> None:
    m = ev.metrics
    metrics.log_metrics(m)  # also pretty-prints to the worker log
    sid = getattr(m, "speech_id", None)
    if not sid:
        return
    rec = _turns.setdefault(sid, {})
    if isinstance(m, metrics.EOUMetrics):
        rec["eou"] = m.end_of_utterance_delay
        rec["tr"] = m.transcription_delay
    elif isinstance(m, metrics.LLMMetrics):
        rec["llm"] = m.ttft
    elif isinstance(m, metrics.TTSMetrics):
        if rec.get("written"):
            return  # only the first TTS segment of a turn = time-to-first-audio
        rec["written"] = True
        eou, tr = rec.get("eou") or 0.0, rec.get("tr") or 0.0
        llm, tts = rec.get("llm") or 0.0, m.ttfb or 0.0
        total = eou + llm + tts
        _write_latency_row([
            datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), sid,
            f"{eou:.3f}", f"{tr:.3f}", f"{llm:.3f}", f"{tts:.3f}", f"{total:.3f}",
        ])
        _turns.pop(sid, None)

# Persona + hard rules (ported from the Twilio server's BASE_RULES).
BASE_RULES = """You are Aarya, a warm, empathetic AI customer-support agent for Vetic Pet Care.
This is an OUTBOUND service-recovery (win-back) call to a customer who gave unhappy
feedback about a recent grooming appointment.

TONE: Calm, warm, sincere. The customer is upset — never sound cheerful, salesy, or
robotic. Stay one notch calmer than the customer and guide them toward calm. Speak in
short, natural Indian-English sentences (1-2 sentences per turn).

HARD RULES:
- English only. Do not use markdown, symbols, emojis, or filler sounds (no "umm").
- Never invent facts, policies, or solutions. If unsure, say a manager will follow up.
- VERIFY details from the customer record (below) in your own words; never ask the
  customer for their name, pet name, phone number, clinic, or date — you already have them.
- Never say "appreciate". Use genuine empathy: "I understand", "I'm so sorry that happened".
"""

# Consolidated call flow (the four Twilio stages, rewritten WITHOUT spoken control tags).
CALL_FLOW = """CALL FLOW — move through these naturally, one question at a time:
1. INTRODUCTION: Confirm you're speaking to the right person, ask if it's a good time,
   ask warmly how their pet is doing, then say you're calling about their unhappy
   grooming feedback and invite them to share what happened. If it's a bad time, agree a
   callback time and use the end_call tool.
2. LISTEN & UNDERSTAND: Let them tell the story. Ask ONE question at a time. Acknowledge
   briefly ("I understand", "I see") and read the complaint back in your own words to
   confirm you've understood, before moving on. Do not offer a fix yet.
3. RESOLUTION: Lead with empathy. Apologise once, sincerely, only if Vetic was at fault.
   Take ownership, no excuses. Offer a concrete next step — raise an escalation ticket
   with the grooming/clinic team and promise a callback or resolution within 2-3 hours.
   Then ask "Is there anything else I can do for you?".
4. CONCLUSION: When the customer signals the call is over (e.g. "no, that's all",
   "thank you", "bye"), call the end_call tool. The tool itself speaks the goodbye and
   hangs up, so do NOT speak your own separate goodbye — just call end_call.
"""


def build_instructions(user_context: str) -> str:
    parts = [BASE_RULES]
    if user_context:
        parts.append(user_context)
    parts.append(CALL_FLOW)
    return "\n\n".join(parts)


class VeticAgent(Agent):
    def __init__(self, instructions: str, greeting: str, owner_name: str = "") -> None:
        super().__init__(
            instructions=instructions,
            stt=sarvam.STT(
                model=os.environ.get("SARVAM_STT_MODEL", "saaras:v3"),
                language=os.environ.get("SARVAM_STT_LANGUAGE", "en-IN"),
                mode="transcribe",
                flush_signal=True,  # enables start/end-of-speech events for turn-taking
                # VAD tuning to ignore BACKGROUND human voices (the caller is louder/closer
                # at the mic). All env-tunable; None = Sarvam default.
                high_vad_sensitivity=_env_bool("SARVAM_HIGH_VAD_SENSITIVITY", False),
                positive_speech_threshold=_env_num("SARVAM_POSITIVE_SPEECH_THRESHOLD", float),
                start_speech_volume_threshold=_env_num("SARVAM_START_VOLUME_THRESHOLD", float),
                min_speech_frames=_env_num("SARVAM_MIN_SPEECH_FRAMES", int),
                first_turn_min_speech_frames=_env_num("SARVAM_FIRST_TURN_MIN_SPEECH_FRAMES", int),
            ),
            llm=anthropic.LLM(model=os.environ.get("LLM_MODEL", "claude-haiku-4-5-20251001")),
            tts=sarvam.TTS(
                target_language_code=os.environ.get("SARVAM_TARGET_LANG", "en-IN"),
                model=os.environ.get("SARVAM_TTS_MODEL", "bulbul:v3"),
                speaker=os.environ.get("SARVAM_SPEAKER", "priya"),
                speech_sample_rate=int(os.environ.get("SARVAM_SAMPLE_RATE", "22050")),
            ),
        )
        self._greeting = greeting
        self._owner_name = owner_name

    async def on_enter(self) -> None:
        # Deterministic opening line (exact text, not LLM-generated).
        await self.session.say(self._greeting)

    @function_tool
    async def end_call(self, ctx: RunContext) -> None:
        """End the call when the customer signals the conversation is over (e.g. "no,
        that's all", "thank you", "bye"). This SPEAKS a brief goodbye, waits for it to
        finish playing, and only THEN hangs up — so the caller always hears a close-out."""
        logger.info("end_call tool invoked — speaking goodbye, then closing")
        bye = (
            f"Thank you, {self._owner_name}. Take care, and we'll be in touch shortly. Goodbye."
            if self._owner_name
            else "Thank you. Take care, and we'll be in touch shortly. Goodbye."
        )
        try:
            # Speak the goodbye and WAIT until it has fully finished playing (not just
            # started) before hanging up — otherwise deleting the room clips it.
            handle = ctx.session.say(bye, allow_interruptions=False)
            await handle.wait_for_playout()
        except Exception as e:
            logger.warning("goodbye playback failed: %s", e)
        # End the call for everyone — this also disconnects the browser caller, which is
        # what flips the page UI to "ended" and stops/uploads the recording. Deleting the
        # room tears the session down cleanly, so we do NOT call session.aclose() (that was
        # what triggered the "speech not done in time" cancellation during teardown).
        try:
            room_name = get_job_context().room.name
            lkapi = api.LiveKitAPI()
            await lkapi.room.delete_room(api.DeleteRoomRequest(room=room_name))
            await lkapi.aclose()
            logger.info("deleted room %s to end the call for the caller", room_name)
        except Exception as e:
            logger.warning("delete_room failed: %s", e)


async def entrypoint(ctx: JobContext) -> None:
    await ctx.connect()

    identifier = os.environ.get("TEST_CALL_IDENTIFIER", "352027")
    user_context = get_user_context(identifier)
    name = owner_first_name(user_context)
    greeting = (
        f"Good morning, am I speaking with {name}? This is Aarya calling from Vetic."
        if name
        else "Good morning, this is Aarya calling from Vetic. May I confirm who I'm speaking with?"
    )

    # Sarvam STT does its own VAD/endpointing → turn_detection="stt", no separate VAD.
    session = AgentSession(turn_detection="stt", min_endpointing_delay=0.07)
    session.on("metrics_collected", _on_metrics)  # write per-turn latency to latency_logs.csv
    await session.start(
        agent=VeticAgent(build_instructions(user_context), greeting, name),
        room=ctx.room,
        room_input_options=RoomInputOptions(
            # Krisp noise cancellation: removes background NOISE and other VOICES from the
            # caller's audio before it reaches Sarvam STT (runs on LiveKit Cloud).
            noise_cancellation=noise_cancellation.BVC(),
        ),
    )


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
