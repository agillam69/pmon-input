# Windows Setup and PM2 Deployment Script for CFA-PagerMon Bridge
$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $ProjectDir

Write-Host "=== Setting up CFA-PagerMon Bridge in $ProjectDir ===" -ForegroundColor Cyan

# 1. Check Python
$PythonExe = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $PythonExe) {
    Write-Error "Python 3 is required but was not found in PATH."
}
Write-Host "[✓] Python found: $PythonExe" -ForegroundColor Green

# 2. Setup Virtual Environment
if (-not (Test-Path ".venv")) {
    Write-Host "Creating Python virtual environment in .venv..." -ForegroundColor Yellow
    python -m venv .venv
}
Write-Host "[✓] Virtual environment ready" -ForegroundColor Green

# 3. Install Dependencies
Write-Host "Installing dependencies..." -ForegroundColor Yellow
& ".\.venv\Scripts\python.exe" -m pip install --upgrade pip
& ".\.venv\Scripts\python.exe" -m pip install -r requirements-dev.txt
Write-Host "[✓] Dependencies installed" -ForegroundColor Green

# 4. Check for .env file
if (-not (Test-Path ".env")) {
    Write-Host "Creating .env from .env.example..." -ForegroundColor Yellow
    Copy-Item ".env.example" ".env"
    Write-Host "[!] Please configure your PAGERMON_API_KEY in .env before enabling live delivery." -ForegroundColor Yellow
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
Write-Host "[✓] All tests passed" -ForegroundColor Green

# 7. Run Bridge Check
Write-Host "Running Bridge Diagnostic Check..." -ForegroundColor Yellow
& ".\.venv\Scripts\python.exe" -m src.cfa_pagermon_bridge.main --check

Write-Host ""
Write-Host "=== Setup Complete ===" -ForegroundColor Cyan
Write-Host "To start the bridge with PM2:" -ForegroundColor White
Write-Host "  pm2 start ecosystem.config.js" -ForegroundColor Green
Write-Host "  pm2 save" -ForegroundColor Green
Write-Host "To view logs:" -ForegroundColor White
Write-Host "  pm2 logs cfa-pagermon-bridge" -ForegroundColor Green
