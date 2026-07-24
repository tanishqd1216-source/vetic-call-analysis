---
name: make-call
description: "Triggered when the user asks you to 'make a call', 'start a call', or boot up the calling environment."
---

# Make a Call Skill

When the user asks you to make a call or start the call environment, you must follow these steps to ensure a flawless startup without port conflicts:

1. **Clean up existing processes** to prevent address-in-use errors:
   - Use `run_command` to find and kill any process using port 8080: `netstat -ano | findstr :8080` then `taskkill /PID <pid> /F` (or similar).
   - Alternatively, kill python processes and ngrok if it's safe to do so.

2. **Start the Webcall Server**:
   - Use `run_command` in the background.
   - Command: `.venv\Scripts\python webcall_server.py`
   - Cwd: `c:\Users\hp\Documents\claude_WO_API\livekit_agent`
   - WaitMsBeforeAsync: 2000

3. **Start the LiveKit Agent**:
   - Use `run_command` in the background.
   - Command: `.venv\Scripts\python agent.py dev`
   - Cwd: `c:\Users\hp\Documents\claude_WO_API\livekit_agent`
   - WaitMsBeforeAsync: 2000

4. **Start the Ngrok Tunnel**:
   - Make sure ngrok is configured with the auth token: `ngrok config add-authtoken 3Gln6ZmEfVnoZmiQOW6V9QeA13I_3CiGvPRErQ3p45kk1odjX`
   - Start ngrok in the background: `C:\Users\hp\AppData\Local\Microsoft\WinGet\Packages\Ngrok.Ngrok_Microsoft.Winget.Source_8wekyb3d8bbwe\ngrok.exe http 8080 --domain overdrawn-slush-refute.ngrok-free.dev`
   - Cwd: `c:\Users\hp\Documents\claude_WO_API`

5. **Wait 3 seconds** to ensure everything is booted up.

6. **Inform the user** that the LiveKit Voice Agent environment and webcall server are now running and accessible publicly at `https://overdrawn-slush-refute.ngrok-free.dev`.

7. If the user also wants YOU to make the call automatically, use the `browser_subagent` to open `https://overdrawn-slush-refute.ngrok-free.dev`, click 'Start Call', and engage. Otherwise, just tell them it's ready.
