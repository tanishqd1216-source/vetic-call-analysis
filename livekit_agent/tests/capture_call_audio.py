"""Places a real call against the currently-running worker (same pattern as
live_call_smoke_test.py: /token -> rtc.Room().connect()) and, unlike that
smoke test, actually captures the agent's synthesized audio to a WAV file so
it can be run through analyze_call_audio.py.

Usage:
    python capture_call_audio.py <agent_label> [--observe-seconds N]

`agent_label` is just used to name the output file (e.g. "outbound", "inbound") --
this script doesn't start/stop workers itself (see run_verified_loop.py for that).
"""
import argparse
import asyncio
import json
import os
import time
import urllib.request
import wave

from livekit import rtc

TOKEN_URL = "http://127.0.0.1:8080/token"
HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "results", "audio_capture")


async def capture(agent_label: str, observe_seconds: int = 30) -> str:
    os.makedirs(OUT_DIR, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(OUT_DIR, f"{agent_label}_{ts}.wav")

    with urllib.request.urlopen(TOKEN_URL) as resp:
        data = json.loads(resp.read())
    token, url, room_name = data["token"], data["url"], data["room"]
    print(f"[{time.strftime('%H:%M:%S')}] Minted token for room '{room_name}', connecting to {url} ...")

    room = rtc.Room()
    frames = []
    frame_sample_rate = {"value": None}
    capture_done = asyncio.Event()

    async def read_track(track: rtc.Track):
        stream = rtc.AudioStream(track, sample_rate=48000, num_channels=1)
        try:
            async for event in stream:
                frame = event.frame
                frame_sample_rate["value"] = frame.sample_rate
                frames.append(bytes(frame.data))
        except Exception as e:
            print(f"[{time.strftime('%H:%M:%S')}] audio stream ended: {e}")
        finally:
            await stream.aclose()

    def on_track_subscribed(track, publication, participant):
        if track.kind == rtc.TrackKind.KIND_AUDIO and participant.identity != "web-caller":
            print(f"[{time.strftime('%H:%M:%S')}] Subscribed to agent audio track ({participant.identity})")
            asyncio.create_task(read_track(track))

    def on_disconnected(reason=None):
        print(f"[{time.strftime('%H:%M:%S')}] Room disconnected ({reason}) -- agent likely ended the call.")
        capture_done.set()

    room.on("track_subscribed", on_track_subscribed)
    room.on("disconnected", on_disconnected)

    await room.connect(url, token)
    print(f"[{time.strftime('%H:%M:%S')}] Connected as '{room.local_participant.identity}'. "
          f"Waiting up to {observe_seconds}s for greeting + first turn...")

    try:
        await asyncio.wait_for(capture_done.wait(), timeout=observe_seconds)
    except asyncio.TimeoutError:
        print(f"[{time.strftime('%H:%M:%S')}] Observe window elapsed without the agent ending the call.")

    try:
        await room.disconnect()
    except Exception:
        pass

    sr = frame_sample_rate["value"] or 48000
    pcm = b"".join(frames)
    with wave.open(out_path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm)

    duration = len(pcm) / 2 / sr if sr else 0
    print(f"[{time.strftime('%H:%M:%S')}] Wrote {out_path} ({duration:.2f}s @ {sr}Hz, {len(frames)} frames)")
    return out_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("agent_label", help="Label for the output filename, e.g. outbound/inbound")
    parser.add_argument("--observe-seconds", type=int, default=30)
    args = parser.parse_args()
    asyncio.run(capture(args.agent_label, args.observe_seconds))
