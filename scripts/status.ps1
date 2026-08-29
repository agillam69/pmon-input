# Windows Service Status Check for CFA-PagerMon Bridge
$ProjectDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $ProjectDir

Write-Host "=== CFA-PagerMon Bridge Status ===" -ForegroundColor Cyan

# 1. PM2 Status
if (Get-Command pm2 -ErrorAction SilentlyContinue) {
    Write-Host "`n-- PM2 Process Status --" -ForegroundColor Yellow
    pm2 status bridge
}

# 2. State Database Counts
if (Test-Path ".venv\Scripts\python.exe") {
    Write-Host "`n-- Database Diagnostic Check --" -ForegroundColor Yellow
    & ".\.venv\Scripts\python.exe" -m src.cfa_pagermon_bridge.main --check
}
