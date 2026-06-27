# get_tunnel_url.ps1  —  NEW helper. Starts a cloudflared quick tunnel to your
# local server, captures the public https URL, and saves it to tunnel_url.txt so
# make_call_env.py can read it. Also prints the exact Twilio webhook to paste.
#
# It does NOT modify any existing file. It writes two NEW files:
#   - tunnel_url.txt       (the current public URL, one line)
#   - tunnel_capture.log   (cloudflared output, for debugging)
#
# Run it from this folder:   powershell -ExecutionPolicy Bypass -File .\get_tunnel_url.ps1

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$exe  = Join-Path $here "cloudflared.exe"
$log  = Join-Path $here "tunnel_capture.log"
$out  = Join-Path $here "tunnel_url.txt"
$port = 8000

if (-not (Test-Path $exe)) { throw "cloudflared.exe not found in $here" }

# Optional: stop other cloudflared instances so you don't end up juggling several
# dead URLs. Comment this out if you want to keep them running.
Get-Process cloudflared -ErrorAction SilentlyContinue | ForEach-Object {
    Write-Host "Stopping old cloudflared (PID $($_.Id))..."
    try { Stop-Process -Id $_.Id -Force } catch {}
}

if (Test-Path $log) { Remove-Item $log -Force }

Write-Host "Starting cloudflared quick tunnel -> http://localhost:$port ..."
$proc = Start-Process -FilePath $exe `
    -ArgumentList @("tunnel", "--url", "http://localhost:$port", "--logfile", $log) `
    -PassThru -WindowStyle Hidden

# Poll the log for the assigned URL (usually appears within a few seconds).
$url = $null
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 1
    if (Test-Path $log) {
        $m = Select-String -Path $log -Pattern "https://[a-z0-9-]+\.trycloudflare\.com" -AllMatches |
             ForEach-Object { $_.Matches.Value } | Select-Object -Last 1
        if ($m) { $url = $m; break }
    }
}

if (-not $url) {
    Write-Host "Could not capture a tunnel URL. Check the log: $log"
    exit 1
}

Set-Content -Path $out -Value $url -Encoding ascii -NoNewline

Write-Host ""
Write-Host "============================================================"
Write-Host " Live tunnel URL : $url"
Write-Host " Saved to        : tunnel_url.txt"
Write-Host ""
Write-Host " PASTE THIS into Twilio (Phone Numbers > your number >"
Write-Host " 'A call comes in', method POST):"
Write-Host "      $url/incoming_call"
Write-Host ""
Write-Host " Then place a call with:   python make_call_env.py"
Write-Host "============================================================"
Write-Host ""
Write-Host "cloudflared (PID $($proc.Id)) is tunneling :$port in the background."
Write-Host "Stop it later with:   Stop-Process -Id $($proc.Id)"
