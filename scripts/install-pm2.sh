#!/usr/bin/env bash
# Git-pull, PM2-managed installer for CFA-PagerMon Bridge (Linux/macOS)
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "=== CFA-PagerMon Bridge PM2 installer ==="
echo "Project directory: $PROJECT_DIR"

# 1. Check Python
if ! command -v python3 &>/dev/null; then
    echo "ERROR: python3 is not installed."
    exit 1
fi

# 2. Create virtual environment
if [ ! -d "$PROJECT_DIR/.venv" ]; then
    echo "Creating Python virtual environment..."
    python3 -m venv "$PROJECT_DIR/.venv"
fi

# 3. Install dependencies
echo "Installing dependencies..."
"$PROJECT_DIR/.venv/bin/pip" install --upgrade pip
"$PROJECT_DIR/.venv/bin/pip" install -r "$PROJECT_DIR/requirements.txt"

# 4. Create logs directory
mkdir -p "$PROJECT_DIR/logs"

# 5. Create .env in dry-run mode if missing
if [ ! -f "$PROJECT_DIR/.env" ]; then
    echo "Creating .env from .env.example (dry-run mode)..."
    cp "$PROJECT_DIR/.env.example" "$PROJECT_DIR/.env"
    sed -i 's/DRY_RUN=false/DRY_RUN=true/' "$PROJECT_DIR/.env"
    echo "[INFO] The bridge will start in dry-run mode."
    echo "       Edit $PROJECT_DIR/.env or use the web UI at http://<host>:8585"
    echo "       to set PAGERMON_API_KEY, then restart or disable dry-run."
fi
chmod 600 "$PROJECT_DIR/.env"

# 6. Run tests
echo "Running test suite..."
"$PROJECT_DIR/.venv/bin/pytest" -v "$PROJECT_DIR/tests"

# 7. Run a quick check
echo "Running bridge check..."
"$PROJECT_DIR/.venv/bin/python" -m src.cfa_pagermon_bridge.main --check

# 8. Ensure PM2 is installed
if ! command -v pm2 &>/dev/null; then
    echo "ERROR: PM2 is not installed. Install it with:"
    echo "  npm install -g pm2"
    exit 1
fi

# 9. Start/restart with PM2
echo "Starting bridge with PM2..."
cd "$PROJECT_DIR"
pm2 startOrRestart ecosystem.config.js

# 10. Save PM2 config so it restarts on boot
echo "Saving PM2 process list..."
pm2 save

echo ""
echo "=== Installation complete ==="
echo "Web UI:     http://$(hostname -I 2>/dev/null | awk '{print $1}' || echo 'localhost'):8585"
echo "Status:     pm2 status"
echo "Logs:       pm2 logs cfa-pagermon-bridge"
echo "Stop:       pm2 stop cfa-pagermon-bridge"
echo "Restart:    pm2 restart cfa-pagermon-bridge"
