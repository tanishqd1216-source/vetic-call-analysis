"""Tiny web-call front-end for the LiveKit Vetic agent.

Serves a one-button page (webcall.html) and mints a LiveKit access token so the
browser can join a fresh room. The running `python agent.py dev` worker auto-dispatches
Aarya into that room. When Aarya finishes (its end_call tool fires), the agent leaves
the room and the page detects that and ends the call automatically.

Run:  python webcall_server.py   →  open http://localhost:8080
"""
import asyncio
import datetime
import json
import os
import secrets
import shutil
import subprocess
import sys

from dotenv import load_dotenv, dotenv_values, set_key
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from livekit import api
import vetic_db
import chromadb

load_dotenv()
app = FastAPI()
HERE = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(HERE, ".env")

# Global to hold the agent subprocess, and which mode it's running as (dynamic /
# scripted / inbound) — only one worker runs at a time, since agent.py and
# agent_inbound.py both register with LiveKit under the same empty agent_name, and
# having two workers live at once makes room dispatch ambiguous.
agent_process = None
agent_mode = None

# Where browser call recordings are saved (both sides mixed, named by timestamp).
REC_DIR = os.environ.get("RECORDINGS_DIR", r"C:\Users\hp\Documents\recordings\_Unknown")


def _ffmpeg_exe():
    """Path to an ffmpeg binary — the pip-bundled one (imageio-ffmpeg) or one on PATH."""
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return shutil.which("ffmpeg")


def _convert_to_mp3(src: str, dst: str):
    exe = _ffmpeg_exe()
    if not exe:
        return False, "ffmpeg not available"
    try:
        subprocess.run(
            [exe, "-y", "-i", src, "-vn", "-ac", "1", "-ar", "44100", "-b:a", "128k", dst],
            check=True, capture_output=True,
        )
        return True, None
    except subprocess.CalledProcessError as e:
        return False, (e.stderr or b"").decode(errors="ignore")[-300:]


@app.post("/recording")
async def save_recording(request: Request):
    """Receive the browser-mixed call audio (caller + Aarya), save it, and convert to mp3.
    This runs AFTER the call has ended (on upload), so it never affects call latency."""
    ts = request.query_params.get("ts") or datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    ts = "".join(c for c in ts if c.isalnum() or c in "_-")  # sanitize filename
    os.makedirs(REC_DIR, exist_ok=True)
    data = await request.body()
    webm_path = os.path.join(REC_DIR, f"{ts}.webm")
    with open(webm_path, "wb") as f:
        f.write(data)

    mp3_path = os.path.join(REC_DIR, f"{ts}.mp3")
    ok, err = await asyncio.to_thread(_convert_to_mp3, webm_path, mp3_path)  # off the event loop
    if ok:
        try:
            os.remove(webm_path)  # keep only the mp3
        except OSError:
            pass
        print(f"[recording] saved -> {mp3_path}")
        return JSONResponse({"saved": mp3_path, "format": "mp3", "bytes": len(data)})
    print(f"[recording] mp3 convert failed ({err}); kept {webm_path}")
    return JSONResponse({"saved": webm_path, "format": "webm", "convert_error": err})


@app.get("/")
async def index():
    """Landing view — the agent selector (Outbound / Inbound)."""
    with open(os.path.join(HERE, "landing.html"), encoding="utf-8") as f:
        return HTMLResponse(f.read())


@app.get("/outbound")
async def outbound():
    """The existing outbound call console, unchanged — just reachable at its own path now."""
    with open(os.path.join(HERE, "webcall.html"), encoding="utf-8") as f:
        return HTMLResponse(f.read())


@app.get("/inbound")
async def inbound():
    """Live inbound-call ops dashboard: idle until a caller is identified, then shows
    their record and live phase/category/resolution as agent_inbound.py reports it."""
    with open(os.path.join(HERE, "inbound_dashboard.html"), encoding="utf-8") as f:
        return HTMLResponse(f.read())


@app.get("/token")
async def token():
    """Mint a token for a brand-new room so each call gets a fresh agent session."""
    room = "web-call-" + secrets.token_hex(4)
    grant = api.VideoGrants(room_join=True, room=room, can_publish=True, can_subscribe=True)
    at = (
        api.AccessToken(os.environ["LIVEKIT_API_KEY"], os.environ["LIVEKIT_API_SECRET"])
        .with_identity("web-caller")
        .with_name("Web Caller")
        .with_grants(grant)
    )
    return JSONResponse({"token": at.to_jwt(), "url": os.environ["LIVEKIT_URL"], "room": room})


# --- Agent Orchestration Endpoints ---

def _stop_agent_process():
    """Terminate the current agent subprocess (if any) and clear tracking globals."""
    global agent_process, agent_mode
    if agent_process is not None and agent_process.poll() is None:
        try:
            agent_process.terminate()
            try:
                agent_process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                agent_process.kill()
        except Exception:
            pass
    agent_process = None
    agent_mode = None


@app.get("/api/agent/status")
async def get_agent_status():
    global agent_process, agent_mode
    if agent_process is None:
        return JSONResponse({"status": "stopped", "mode": None})

    # Check if process is still running
    if agent_process.poll() is None:
        return JSONResponse({"status": "running", "mode": agent_mode})
    else:
        # Process died
        agent_process = None
        agent_mode = None
        return JSONResponse({"status": "stopped", "mode": None})


@app.post("/api/agent/start")
async def start_agent(request: Request):
    global agent_process, agent_mode

    try:
        body = await request.json()
    except Exception:
        body = {}
    mode = body.get("mode", "dynamic")

    if agent_process is not None and agent_process.poll() is None:
        if agent_mode == mode:
            return JSONResponse({"status": "running", "mode": mode, "message": "Agent is already running"})
        # A different agent is running (e.g. inbound while outbound was requested) —
        # only one worker may run at a time (see the global comment above), so swap.
        _stop_agent_process()

    try:
        env = os.environ.copy()
        os.makedirs(os.path.join(HERE, "logs"), exist_ok=True)
        log_file = open(os.path.join(HERE, "logs", "agent_output.log"), "w", encoding="utf-8")

        if mode == "scripted":
            script_path = os.path.join(os.path.dirname(HERE), "script", "scripted_agent.py")
            cmd = [sys.executable, script_path, "dev"]
        elif mode == "inbound":
            cmd = [sys.executable, "agent_inbound.py", "dev"]
        else:
            cmd = [sys.executable, "agent.py", "dev"]

        agent_process = subprocess.Popen(
            cmd,
            cwd=HERE,
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0
        )
        agent_mode = mode
        return JSONResponse({"status": "running", "mode": mode, "message": f"{mode.capitalize()} agent started successfully"})
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)


@app.post("/api/agent/stop")
async def stop_agent():
    if agent_process is None or agent_process.poll() is not None:
        _stop_agent_process()
        return JSONResponse({"status": "stopped", "message": "Agent is already stopped"})

    try:
        _stop_agent_process()
        return JSONResponse({"status": "stopped", "message": "Agent stopped successfully"})
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

# --- Configuration Endpoints ---

@app.get("/api/config")
async def get_config():
    # Read directly from .env file to get current state on disk
    config = dotenv_values(ENV_PATH)
    return JSONResponse(config)

@app.post("/api/config")
async def update_config(request: Request):
    data = await request.json()
    for key, value in data.items():
        if value is not None:
            set_key(ENV_PATH, key, str(value))
    
    # Reload environment in the current process so new agent spawns get it
    load_dotenv(ENV_PATH, override=True)
    return JSONResponse({"status": "success", "message": "Configuration updated successfully"})

# --- Inbound live-dashboard state ---
# One-way telemetry file written by agent_inbound.py (see DASHBOARD_STATE_PATH /
# _write_dashboard_state there) — this endpoint just reads it back for the browser.
# Never written to from here, so the dashboard has no path back into the agent.
INBOUND_STATE_PATH = os.path.join(HERE, "dashboard_state.json")
_IDLE_STATE = {
    "active": False, "identified": False, "phase": "Idle",
    "category": "", "resolution": "",
    "owner": "", "pet": "", "service": "", "date": "", "clinic": "", "city": "",
    "updated_at": None,
}


@app.get("/api/inbound/state")
async def get_inbound_state():
    """Live state of whatever call agent_inbound.py is currently (or most recently) handling."""
    if not os.path.exists(INBOUND_STATE_PATH):
        return JSONResponse(_IDLE_STATE)
    try:
        with open(INBOUND_STATE_PATH, "r", encoding="utf-8") as f:
            return JSONResponse(json.load(f))
    except Exception as e:
        return JSONResponse({**_IDLE_STATE, "error": str(e)})


# --- Advanced Data Endpoints ---

@app.get("/api/user_info")
async def get_user_info(identifier: str):
    """Fetch user profile from SQLite for the UI"""
    context = vetic_db.get_user_context(identifier)
    raw_data = vetic_db.get_user_raw(identifier)
    if not context and not raw_data:
        return JSONResponse({"error": "User not found"})
    return JSONResponse({"context_text": context, "raw_data": raw_data})

@app.get("/api/latency")
async def get_latency():
    """Fetch recent latency logs"""
    log_path = os.environ.get("LATENCY_CSV_PATH") or os.path.join(HERE, "logs", "latency_logs_outbound.csv")
    if not os.path.exists(log_path):
        return JSONResponse({"error": "No latency logs found"})
    try:
        with open(log_path, "r") as f:
            lines = f.readlines()
            # Return last 10 logs + header
            header = lines[0] if lines else ""
            logs = lines[-10:] if len(lines) > 1 else []
            return JSONResponse({"header": header, "logs": logs})
    except Exception as e:
        return JSONResponse({"error": str(e)})


def _latency_bucket(v: float) -> str:
    if v < 1.0:
        return "instant"
    if v < 2.5:
        return "acceptable"
    if v < 5.0:
        return "sluggish"
    return "broken"


def _read_latency_stats(log_path: str, recent: int = 40, rolling: int = 20) -> dict:
    """Shared CSV-to-stats reader behind both /api/latency_live (outbound) and
    /api/inbound/latency_live — reads the whole file each time, which is fine at
    these files' current size (~1-2k rows); re-tail from the end instead if it
    ever grows huge."""
    if not os.path.exists(log_path):
        return {"error": f"No latency log at {log_path}", "rows_total": 0}

    import csv as csv_mod
    import statistics
    import time as time_mod

    rows = []
    try:
        with open(log_path, "r", newline="", encoding="utf-8", errors="ignore") as f:
            for r in csv_mod.DictReader(f):
                try:
                    total = float(r["Response_latency_s"])
                except (KeyError, ValueError):
                    continue
                rows.append({
                    "t": r.get("Timestamp", ""),
                    "call_id": r.get("Call_ID", ""),
                    "turn": int(r.get("Turn") or 0),
                    "id": r.get("Speech_ID", ""),
                    "eou": float(r.get("EOU_delay_s") or 0),
                    "tr": float(r.get("Transcription_delay_s") or 0),
                    "llm": float(r.get("LLM_TTFT_s") or 0),
                    "tts": float(r.get("TTS_TTFB_s") or 0),
                    "total": total,
                    "bucket": _latency_bucket(total),
                    # Absent on rows written before token logging was added.
                    "in_tok": int(r.get("Input_Tokens") or 0),
                    "out_tok": int(r.get("Output_Tokens") or 0),
                })
    except Exception as e:
        return {"error": str(e)}

    total_count = len(rows)
    recent_rows = rows[-recent:] if recent > 0 else rows
    rolling_rows = rows[-rolling:] if rolling > 0 else rows
    rolling_totals = [r["total"] for r in rolling_rows]

    rolling_stats = None
    if rolling_totals:
        sorted_vals = sorted(rolling_totals)
        rolling_stats = {
            "count": len(rolling_totals),
            "mean": round(sum(rolling_totals) / len(rolling_totals), 3),
            "p50": round(statistics.median(sorted_vals), 3),
            "p95": round(sorted_vals[max(0, int(len(sorted_vals) * 0.95) - 1)], 3),
        }

    latest = rows[-1] if rows else None
    seconds_since_last = None
    if latest and latest["t"]:
        try:
            last_dt = time_mod.strptime(latest["t"], "%Y-%m-%d %H:%M:%S")
            seconds_since_last = round(time_mod.time() - time_mod.mktime(last_dt), 1)
        except ValueError:
            pass

    return {
        "rows_total": total_count,
        "recent": recent_rows,
        "rolling": rolling_stats,
        "latest": latest,
        "seconds_since_last_turn": seconds_since_last,
        "server_time": time_mod.strftime("%Y-%m-%d %H:%M:%S"),
    }


@app.get("/api/latency_live")
async def get_latency_live(recent: int = 40, rolling: int = 20):
    """Live view over the latency CSV the running OUTBOUND agent (agent.py) writes
    to (LATENCY_CSV_PATH). Polled by /live and the outbound console every couple
    seconds during a call."""
    log_path = os.environ.get("LATENCY_CSV_PATH") or os.path.join(
        HERE, "logs", "latency_logs_outbound.csv"
    )
    return JSONResponse(_read_latency_stats(log_path, recent, rolling))


@app.get("/api/inbound/latency_live")
async def get_inbound_latency_live(recent: int = 40, rolling: int = 20):
    """Same as /api/latency_live, but reads the INBOUND agent's own latency CSV
    (agent_inbound.py writes to INBOUND_LATENCY_CSV_PATH / logs/latency_logs_inbound.csv)
    so outbound and inbound call metrics never mix."""
    log_path = os.environ.get("INBOUND_LATENCY_CSV_PATH") or os.path.join(
        HERE, "logs", "latency_logs_inbound.csv"
    )
    return JSONResponse(_read_latency_stats(log_path, recent, rolling))


@app.get("/live")
async def live_dashboard():
    """Live-polling latency dashboard — open this in a browser tab during a call."""
    with open(os.path.join(HERE, "live_dashboard.html"), encoding="utf-8") as f:
        return HTMLResponse(f.read())

@app.get("/api/db_stats")
async def get_db_stats():
    """Fetch ChromaDB basic stats"""
    db_path = os.path.join(HERE, "knowledge_db")
    try:
        client = chromadb.PersistentClient(path=db_path)
        collection = client.get_collection("vetic_knowledge")
        docs = collection.get()
        return JSONResponse({
            "count": collection.count(),
            "sample_docs": docs["documents"][:5] if docs and "documents" in docs else []
        })
    except Exception as e:
        return JSONResponse({"error": str(e)})

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8080)
