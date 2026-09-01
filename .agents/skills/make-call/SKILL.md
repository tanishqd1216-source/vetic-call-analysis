---
name: make-call
description: "Triggered when the user asks you to 'make a call', 'start a call', or boot up the calling environment."
---

# Make a Call Skill

When the user asks you to make a call or start the call environment, you must follow these steps to ensure a flawless startup without port conflicts:

1. **Clean up existing processes** to prevent address-in-use errors:
   - Use `run_command` to find and kill any process using ports 8080, 3000, and 4000: `netstat -ano | findstr :8080` (repeat for `:3000` and `:4000`) then `taskkill /PID <pid> /F` (or similar).
   - Alternatively, kill python/node processes and ngrok if it's safe to do so.

2. **Start the Webcall Server**:
   - Use `run_command` in the background.
   - Command: `.venv\Scripts\python webcall_server.py`
   - Cwd: `c:\Users\hp\Documents\claude_WO_API\livekit_agent`
   - WaitMsBeforeAsync: 2000
   - **Do not open a browser to `http://localhost:8080` directly** — it's bound to loopback only and is reached through the console's Dashboard panel or the ngrok URL below, never launched on its own.

3. **Start the LiveKit Agent**:
   - Use `run_command` in the background.
   - Command: `.venv\Scripts\python agent_outbound.py dev`
   - Cwd: `c:\Users\hp\Documents\claude_WO_API\livekit_agent`
   - WaitMsBeforeAsync: 2000

4. **Start the Dashboard API backend** (persistence layer used internally by the frontend and by `event_reporter.py`):
   - Use `run_command` in the background.
   - Command: `npm run dev`
   - Cwd: `c:\Users\hp\ai-call-agent\backend`
   - WaitMsBeforeAsync: 2000
   - Listens on `http://localhost:4000`. This is an internal API only — never open it directly in a browser or report it to the user as a link.

5. **Start the Dashboard frontend** (new persistence/history layer):
   - Use `run_command` in the background.
   - Command: `npm run dev`
   - Cwd: `c:\Users\hp\ai-call-agent\frontend`
   - WaitMsBeforeAsync: 2000
   - Listens on `http://localhost:3000`. It reads from the backend started in step 4.

6. **Start the Ngrok Tunnel**:
   - Make sure ngrok is configured with the auth token: `ngrok config add-authtoken 3Gln6ZmEfVnoZmiQOW6V9QeA13I_3CiGvPRErQ3p45kk1odjX`
   - Start ngrok in the background: `C:\Users\hp\AppData\Local\Microsoft\WinGet\Packages\Ngrok.Ngrok_Microsoft.Winget.Source_8wekyb3d8bbwe\ngrok.exe http 8080 --domain overdrawn-slush-refute.ngrok-free.dev`
   - Cwd: `c:\Users\hp\Documents\claude_WO_API`

7. **Wait 3 seconds** to ensure everything is booted up.

8. **Inform the user** with the full set of running services — do not auto-launch or open any of these yourself unless step 9 applies:
   - Webcall console (public): `https://overdrawn-slush-refute.ngrok-free.dev`
   - Dashboard (persistence/history layer): `http://localhost:3000` — also reachable via the "Dashboard" panel inside the webcall console itself, which slides in over the current page instead of navigating away.
   - Dashboard API backend: `http://localhost:4000` — internal only, used by the frontend and `event_reporter.py`; not meant to be opened directly.

9. If the user also wants YOU to make the call automatically, use the `browser_subagent` to open `https://overdrawn-slush-refute.ngrok-free.dev`, click 'Start Call', and engage. Otherwise, just tell them it's ready. Never auto-open `http://localhost:8080` on its own.
