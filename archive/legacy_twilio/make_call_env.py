"""
make_call_env.py  —  NEW, drop-in alternative to make_call.py.

WHY THIS EXISTS
---------------
trycloudflare hands out a BRAND-NEW random URL every time cloudflared restarts.
The old make_call.py has the URL hardcoded, so once the tunnel rotates, Twilio
can no longer reach /incoming_call (call won't connect) or deliver the caller's
transcribed speech to /process_speech (the agent then "can't hear" the caller).

This version reads the current public URL from ONE place instead of hardcoding
it, so you only update it in a single spot when the tunnel changes.

It does NOT modify make_call.py, server.py or .env. To go back, just run the
original make_call.py again.

WHERE THE URL COMES FROM (first match wins)
-------------------------------------------
1) PUBLIC_BASE_URL in your environment / .env   e.g.
       PUBLIC_BASE_URL=https://your-current-id.trycloudflare.com
2) tunnel_url.txt  in this folder  (written automatically by get_tunnel_url.ps1)

HOW TO USE
----------
1) Get the current tunnel URL:
       - run  get_tunnel_url.ps1   (writes tunnel_url.txt for you), OR
       - copy it from the cloudflared window and put it in .env / tunnel_url.txt
2) Set that SAME url + /incoming_call as your Twilio number's Voice webhook
   (Twilio Console > Phone Numbers > your number > "A call comes in").
3) Run:
       python make_call_env.py
   Optionally override the numbers without editing anything:
       set TO_NUMBER=+9198xxxxxxxx   &&  python make_call_env.py   (Windows cmd)
"""

import os
from dotenv import load_dotenv
from twilio.rest import Client

load_dotenv(override=True)


def resolve_base_url() -> tuple[str, str]:
    """Return (base_url, source). Empty url means 'not configured'."""
    url = (os.environ.get("PUBLIC_BASE_URL") or "").strip().rstrip("/")
    if url:
        return url, "PUBLIC_BASE_URL (env/.env)"

    here = os.path.dirname(os.path.abspath(__file__))
    txt = os.path.join(here, "tunnel_url.txt")
    if os.path.exists(txt):
        with open(txt, "r", encoding="utf-8") as f:
            url = f.read().strip().rstrip("/")
        if url:
            return url, "tunnel_url.txt"

    return "", ""


account_sid = os.environ.get("TWILIO_ACCOUNT_SID")
auth_token = os.environ.get("TWILIO_AUTH_TOKEN")
if not account_sid or not auth_token:
    raise RuntimeError(
        "TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN not set. Add them to your .env file."
    )

base_url, source = resolve_base_url()
if not base_url:
    raise SystemExit(
        "\nNo public URL configured.\n"
        "Fix it in ONE of these ways, then re-run:\n"
        "  - run  get_tunnel_url.ps1   (auto-writes tunnel_url.txt), or\n"
        "  - add a line to .env:  PUBLIC_BASE_URL=https://<current>.trycloudflare.com\n"
    )

# Numbers default to your existing values, overridable via env so nothing is hardcoded here.
to_number = os.environ.get("TO_NUMBER", "+918273004988")
from_number = os.environ.get("FROM_NUMBER", "+13198358061")

client = Client(account_sid, auth_token)
print(f"Using Account SID: {account_sid[:5]}...{account_sid[-5:]}")
print(f"Using base URL   : {base_url}   (from {source})")
print(f"Calling          : {to_number}  from  {from_number}")
print("Reminder: the SAME base URL + /incoming_call must be set as the Twilio Voice webhook.\n")

try:
    call = client.calls.create(
        url=f"{base_url}/incoming_call",
        to=to_number,
        from_=from_number,
        record=True,
        recording_status_callback=f"{base_url}/recording_status",
        recording_status_callback_event=["completed"],
    )
    print(f"Call initiated successfully! Call SID: {call.sid}")
except Exception as e:
    print(f"Failed to initiate call: {e}")
