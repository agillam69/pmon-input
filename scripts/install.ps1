# Windows PM2 Setup and Deployment Script for CFA-PagerMon Bridge
$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $ProjectDir

Write-Host "=== Setting up CFA-PagerMon Bridge in $ProjectDir ===" -ForegroundColor Cyan

# 1. Check Python
$PythonExe = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $PythonExe) {
    Write-Error "Python 3 is required but was not found in PATH."
}
Write-Host "[OK] Python found: $PythonExe" -ForegroundColor Green

# 2. Setup Virtual Environment
if (-not (Test-Path ".venv")) {
    Write-Host "Creating Python virtual environment in .venv..." -ForegroundColor Yellow
    python -m venv .venv
}
Write-Host "[OK] Virtual environment ready" -ForegroundColor Green

# 3. Install Dependencies
Write-Host "Installing dependencies..." -ForegroundColor Yellow
& ".\.venv\Scripts\python.exe" -m pip install --upgrade pip
& ".\.venv\Scripts\python.exe" -m pip install -r requirements.txt
Write-Host "[OK] Dependencies installed" -ForegroundColor Green

# 4. Check for .env file (dry-run by default so the web UI can be used)
if (-not (Test-Path ".env")) {
    Write-Host "Creating .env from .env.example in dry-run mode..." -ForegroundColor Yellow
    Copy-Item ".env.example" ".env"
    (Get-Content ".env") -replace '^DRY_RUN=false$', 'DRY_RUN=true' | Set-Content ".env"
    Write-Host "[!] The bridge is running in dry-run mode." -ForegroundColor Yellow
    Write-Host "    Set your PAGERMON_API_KEY via the web UI at http://localhost:8585" -ForegroundColor Yellow
    Write-Host "    or edit .env, then restart: pm2 restart bridge" -ForegroundColor Yellow
}

# 5. Create logs directory
if (-not (Test-Path "logs")) {
    New-Item -ItemType Directory -Path "logs" | Out-Null
}

# 6. Run automated test suite
Write-Host "Running test suite..." -ForegroundColor Yellow
& ".\.venv\Scripts\pytest.exe" -v
if ($LASTEXITCODE -ne 0) {
    Write-Error "Tests failed! Aborting deployment."
}
Write-Host "[OK] All tests passed" -ForegroundColor Green

# 7. Run Bridge Check
Write-Host "Running Bridge Diagnostic Check..." -ForegroundColor Yellow
& ".\.venv\Scripts\python.exe" -m src.cfa_pagermon_bridge.main --check

# 8. Ensure PM2 is available
if (-not (Get-Command pm2 -ErrorAction SilentlyContinue)) {
    Write-Error "PM2 is not installed. Install it first with: npm install -g pm2"
}

# 9. Start / restart with PM2
Write-Host "Starting bridge with PM2..." -ForegroundColor Yellow
& pm2 startOrRestart ecosystem.config.js

# 10. Save PM2 process list
& pm2 save | Out-Null

Write-Host ""
Write-Host "=== Setup Complete ===" -ForegroundColor Cyan
Write-Host "Web UI:     http://localhost:8585" -ForegroundColor White
Write-Host "Status:     pm2 status" -ForegroundColor Green
Write-Host "Logs:       pm2 logs bridge" -ForegroundColor Green
Write-Host "Stop:       pm2 stop bridge" -ForegroundColor Green
Write-Host "Restart:    pm2 restart bridge" -ForegroundColor Green
