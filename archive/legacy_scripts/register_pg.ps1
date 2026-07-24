$binPath = "C:\Program Files\PostgreSQL\18\bin\pg_ctl.exe"
$dataPath = "C:\Program Files\PostgreSQL\18\data"
$serviceName = "postgresql-x64-18"

# Register the service
& $binPath register -N $serviceName -U "NT AUTHORITY\NetworkService" -D $dataPath

# Start the service
Start-Service -Name $serviceName

# Wait a couple of seconds to ensure it starts
Start-Sleep -Seconds 2

# Write status
Get-Service -Name $serviceName | Out-File -FilePath "$env:USERPROFILE\Documents\claude_WO_API\pg_service_status.txt" -Encoding utf8
