# start_call_environment.ps1
Write-Host "Starting Call Environment..."

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
cd $ScriptDir

# Check if port 8080 is in use, and if so, kill the process
$port8080 = Get-NetTCPConnection -LocalPort 8080 -ErrorAction SilentlyContinue
if ($port8080) {
    Write-Host "Port 8080 is in use, killing process..."
    Stop-Process -Id $port8080.OwningProcess -Force -ErrorAction SilentlyContinue
}

# Start webcall_server.py in the background
Write-Host "Starting Webcall Server on port 8080..."
Start-Process -NoNewWindow -FilePath ".venv\Scripts\python.exe" -ArgumentList "webcall_server.py"

# Start agent.py dev in the background
Write-Host "Starting LiveKit Agent..."
Start-Process -NoNewWindow -FilePath ".venv\Scripts\python.exe" -ArgumentList "agent.py dev"

Write-Host "Call environment started successfully. Waiting for services to initialize..."
Start-Sleep -Seconds 3
Write-Host "Ready!"
