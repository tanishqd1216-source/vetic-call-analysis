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
   - Cwd: `D:\vetic-call-analysis-main\livekit_agent`
   - WaitMsBeforeAsync: 2000
   - Binds to loopback only (`http://127.0.0.1:8080`). It is **not** tunneled by ngrok (see step 6) — reach it locally on this machine only, never report it to the user as a public link.

3. **Start the LiveKit Agents**:
   - Use `run_command` in the background, once each for outbound and inbound.
   - Commands: `.venv\Scripts\python agent_outbound.py dev` and `.venv\Scripts\python agent_inbound.py dev`
   - Cwd (both): `D:\vetic-call-analysis-main\livekit_agent`
   - WaitMsBeforeAsync: 2000

4. **Start the Dashboard API backend** (persistence layer used internally by the frontend and by `event_reporter.py`):
   - Use `run_command` in the background.
   - Command: `npm run dev`
   - Cwd: `D:\ai-call-agent-master\backend`
   - WaitMsBeforeAsync: 2000
   - Listens on `http://localhost:4000`. This is an internal API only — never open it directly in a browser or report it to the user as a link.

5. **Start the Dashboard frontend** (Meridian — persistence/history layer):
   - Use `run_command` in the background.
   - Command: `npm run dev`
   - Cwd: `D:\ai-call-agent-master\frontend`
   - WaitMsBeforeAsync: 2000
   - Listens on `http://localhost:3000`. It reads from the backend started in step 4. This is the service tunneled publicly in step 6.

6. **Start the Ngrok Tunnel** (points at the Meridian frontend, port 3000 — not the webcall server):
   - Make sure ngrok is configured with the auth token: `ngrok config add-authtoken 3Gln6ZmEfVnoZmiQOW6V9QeA13I_3CiGvPRErQ3p45kk1odjX`
   - Start ngrok in the background: `C:\Users\tanis\ngrok\ngrok.exe http 3000 --domain overdrawn-slush-refute.ngrok-free.dev`
   - Cwd: `D:\ai-call-agent-master`
   - Verify it actually came up (don't just trust the launch): `curl -s http://127.0.0.1:4040/api/tunnels` and confirm `config.addr` is `http://localhost:3000`.

7. **Wait 3 seconds** to ensure everything is booted up.

8. **Inform the user** with the full set of running services — do not auto-launch or open any of these yourself unless step 9 applies:
   - Meridian dashboard (public): `https://overdrawn-slush-refute.ngrok-free.dev` — this is the ngrok tunnel target; share this link freely.
   - Webcall console (local only, not tunneled): `http://localhost:8080` — this is where calls actually get placed/received. Reachable only on this machine.
   - Dashboard API backend: `http://localhost:4000` — internal only, used by the frontend and `event_reporter.py`; not meant to be opened directly.

9. If the user also wants YOU to make the call automatically, use the `browser_subagent` to open `http://localhost:8080` (the webcall console — **not** the ngrok URL, which now serves the Meridian dashboard, not the call console), click 'Start Call', and engage. Otherwise, just tell them it's ready.
