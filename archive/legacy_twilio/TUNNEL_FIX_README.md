# Fix: "the AI agent can't hear the caller"

## What was actually wrong
Nothing was broken in your speech code. The problem is the **tunnel URL**.

`cloudflared.exe tunnel --url http://localhost:8000` creates a *quick tunnel*,
and trycloudflare assigns a **new random URL every time it restarts**. Your old
`make_call.py` and your Twilio webhook pointed to a URL that has since died, so:

- Twilio could not fetch `/incoming_call` (call won't connect), and
- Twilio could not deliver the caller's transcribed speech to `/process_speech`
  → the agent receives nothing → it looks "deaf."

Verified on your machine: both previously-used URLs
(`universe-spend-proceeding-tracked…` and `summit-sight-hierarchy-closes…`) no
longer resolve. The local server on `:8000` is fine.

## What I added (all NEW files — nothing existing was changed)
| File | Purpose |
|------|---------|
| `make_call_env.py` | Like `make_call.py`, but reads the URL from `PUBLIC_BASE_URL` (.env) or `tunnel_url.txt` instead of hardcoding it. |
| `get_tunnel_url.ps1` | Starts the tunnel, captures the live URL, writes `tunnel_url.txt`, and prints the Twilio webhook to paste. |
| `tunnel_url.txt` | (created when you run the helper) the current public URL. |
| `tunnel_capture.log` | (created when you run the helper) cloudflared output. |

`make_call.py`, `server.py`, and `.env` are **untouched**.
`server.py` needs no change — it already builds its audio URLs from the request
headers, so it adapts to whatever tunnel URL Twilio uses.

## How to use it
1. Start your server if it isn't running:  `python server.py`
2. Get a live tunnel URL:
   ```
   powershell -ExecutionPolicy Bypass -File .\get_tunnel_url.ps1
   ```
   It prints something like `https://abc-def-ghi.trycloudflare.com` and saves it.
3. Paste that URL **+ `/incoming_call`** into Twilio:
   Console → Phone Numbers → your number → "A call comes in" (HTTP POST).
4. Place the call:
   ```
   python make_call_env.py
   ```

The only thing you must update when the tunnel rotates is step 2–3 (re-run the
helper, re-paste the webhook). `make_call_env.py` then picks it up automatically.

## How to confirm this was the cause
Twilio Console → **Monitor → Logs → Errors**. A dead tunnel shows error
**11200 / 11205 (HTTP retrieval/connection failure)** on the webhook.

## How to revert
Just delete these new files (`make_call_env.py`, `get_tunnel_url.ps1`,
`tunnel_url.txt`, `tunnel_capture.log`, this README) and keep using the original
`make_call.py`. Nothing else was modified.

## Permanent fix (optional, recommended)
Quick tunnels will always rotate. To stop this for good, switch to a stable URL:
- a **named Cloudflare tunnel** (permanent hostname; needs a domain on Cloudflare), or
- **ngrok with a free static domain**.
Then set it once as `PUBLIC_BASE_URL` and as the Twilio webhook, and it never
changes again.
