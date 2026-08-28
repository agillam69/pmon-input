#!/usr/bin/env bash
set -euo pipefail

echo "=== CFA-PagerMon Bridge Linux Status ==="

echo -e "\n-- systemd Unit Status --"
systemctl status cfa-pagermon-bridge --no-pager || true

echo -e "\n-- Recent Logs --"
journalctl -u cfa-pagermon-bridge -n 25 --no-pager || true
