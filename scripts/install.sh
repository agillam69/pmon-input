#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "=== Installing CFA-PagerMon Bridge on Linux ==="

# Create dedicated system user if needed
if ! id "pagermon" &>/dev/null; then
    echo "Creating system user 'pagermon'..."
    sudo useradd -r -s /bin/false -d /opt/cfa-pagermon-bridge pagermon || true
fi

# Ensure directories exist
sudo mkdir -p /opt/cfa-pagermon-bridge
sudo mkdir -p /var/lib/cfa-pagermon-bridge
sudo mkdir -p /etc/cfa-pagermon-bridge

# Sync project files
echo "Deploying application to /opt/cfa-pagermon-bridge..."
sudo cp -r "$PROJECT_DIR"/* /opt/cfa-pagermon-bridge/

# Setup environment file if missing
if [ ! -f /etc/cfa-pagermon-bridge/bridge.env ]; then
    echo "Creating default environment file at /etc/cfa-pagermon-bridge/bridge.env..."
    sudo cp /opt/cfa-pagermon-bridge/.env.example /etc/cfa-pagermon-bridge/bridge.env
    sudo chmod 600 /etc/cfa-pagermon-bridge/bridge.env
fi

# Setup Python virtual environment
echo "Configuring Python virtual environment..."
cd /opt/cfa-pagermon-bridge
sudo python3 -m venv .venv
sudo .venv/bin/pip install --upgrade pip
sudo .venv/bin/pip install -r requirements.txt

# Permissions
sudo chown -R pagermon:pagermon /opt/cfa-pagermon-bridge
sudo chown -R pagermon:pagermon /var/lib/cfa-pagermon-bridge

# Install and start systemd unit
echo "Installing systemd service unit..."
sudo cp /opt/cfa-pagermon-bridge/deploy/cfa-pagermon-bridge.service /etc/systemd/system/
sudo systemctl daemon-reload

echo "=== Installation complete ==="
echo "Edit /etc/cfa-pagermon-bridge/bridge.env with your real API key."
echo "Then enable and start the service with:"
echo "  sudo systemctl enable --now cfa-pagermon-bridge"
