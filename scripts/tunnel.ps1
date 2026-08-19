# Opens a public URL to the Vite dev server so a phone away from home can reach the app.
#
# One tunnel is enough for both halves: the browser calls /api on the same origin and Vite
# proxies it to the backend, which stays bound to 127.0.0.1 and is never exposed directly.
#
#   pwsh scripts/tunnel.ps1
#
# The URL is PUBLIC and the app has no login. Anyone who has it can run searches on your
# Anthropic credits and read your saved location. Stop it when you are done.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $root ".env"

if (-not (Get-Command ngrok -ErrorAction SilentlyContinue)) {
    Write-Error "ngrok is not installed. Run: winget install ngrok.ngrok"
}
if (-not (Test-Path $envFile)) {
    Write-Error "No .env at $envFile. Copy .env.example and fill in NGROK_AUTHTOKEN."
}

# only NGROK_AUTHTOKEN is read: the rest of .env holds API keys that have no business in
# this process's environment
foreach ($line in Get-Content $envFile) {
    if ($line -match '^\s*NGROK_AUTHTOKEN\s*=\s*(.+?)\s*$') {
        $env:NGROK_AUTHTOKEN = $Matches[1].Trim('"').Trim("'")
    }
}
if (-not $env:NGROK_AUTHTOKEN) {
    Write-Error "NGROK_AUTHTOKEN is empty in .env. Get one at dashboard.ngrok.com/get-started/your-authtoken"
}

# fail early rather than tunnelling to a dead port and debugging it on the phone
foreach ($port in 5173, 8000) {
    if (-not (Test-NetConnection -ComputerName 127.0.0.1 -Port $port -InformationLevel Quiet -WarningAction SilentlyContinue)) {
        Write-Error "Nothing is listening on $port. Start the backend (uvicorn) and the frontend (npm run dev) first."
    }
}

Write-Host "Tunnelling http://localhost:5173 - the URL below is public and unauthenticated." -ForegroundColor Yellow
ngrok http 5173
