"""Tiny web-call front-end for the LiveKit Vetic agent.

Serves a one-button page (webcall.html) and mints a LiveKit access token so the
browser can join a fresh room. The running `python agent.py dev` worker auto-dispatches
Aarya into that room. When Aarya finishes (its end_call tool fires), the agent leaves
the room and the page detects that and ends the call automatically.

Run:  python webcall_server.py   →  open http://localhost:8080
"""
import asyncio
import datetime
import os
import secrets
import shutil
import subprocess

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from livekit import api

load_dotenv()
app = FastAPI()
HERE = os.path.dirname(os.path.abspath(__file__))

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
    with open(os.path.join(HERE, "webcall.html"), encoding="utf-8") as f:
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


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8080)
