"""One-off smoke test: places a REAL call against the currently-running inbound
agent worker via the actual LiveKit infrastructure (same /token endpoint the
browser uses), and observes the real lifecycle — room creation, worker
auto-dispatch, greeting playback, silence-timeout handling, and call teardown.

No audio is spoken back (no mic/speaker here), so this cannot exercise the
conversation content — it verifies the real connectivity + call-lifecycle path
that a genuine caller would go through, end to end.
"""
import asyncio
import sys
import time
import urllib.request
import json

from livekit import rtc

TOKEN_URL = "http://127.0.0.1:8080/token"
OBSERVE_SECONDS = 60


async def main():
    with urllib.request.urlopen(TOKEN_URL) as resp:
        data = json.loads(resp.read())
    token, url, room_name = data["token"], data["url"], data["room"]
    print(f"[{time.strftime('%H:%M:%S')}] Minted token for room '{room_name}', connecting to {url} ...")

    room = rtc.Room()
    events = []

    def log(event_name, *args):
        msg = f"[{time.strftime('%H:%M:%S')}] EVENT: {event_name} {args if args else ''}"
        print(msg)
        events.append(event_name)

    room.on("participant_connected", lambda p: log("participant_connected", p.identity))
    room.on("participant_disconnected", lambda p: log("participant_disconnected", p.identity))
    room.on("track_published", lambda pub, p: log("track_published", pub.kind, p.identity))
    room.on("track_subscribed", lambda track, pub, p: log("track_subscribed", track.kind, p.identity))
    room.on("disconnected", lambda reason=None: log("disconnected", reason))

    await room.connect(url, token)
    print(f"[{time.strftime('%H:%M:%S')}] Connected as '{room.local_participant.identity}'. Waiting up to {OBSERVE_SECONDS}s for the agent to join, greet, and (since nobody speaks back) time out on silence...")

    start = time.time()
    while time.time() - start < OBSERVE_SECONDS:
        if not room.isconnected():
            print(f"[{time.strftime('%H:%M:%S')}] Room disconnected (agent likely ended the call).")
            break
        await asyncio.sleep(1)

    print(f"[{time.strftime('%H:%M:%S')}] Done observing. Event sequence: {events}")
    try:
        await room.disconnect()
    except Exception:
        pass


if __name__ == "__main__":
    asyncio.run(main())
