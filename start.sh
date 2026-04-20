#!/bin/bash
# Supermicro iKVM Web Console Launcher
# Usage: ./start.sh [BMC_HOST] [USERNAME] [PASSWORD]

BMC_HOST="${1:-192.0.2.11}"
USERNAME="${2:-ADMIN}"
PASSWORD="${3:-ADMIN}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Install dependencies if needed
pip install -q websockets 2>/dev/null || pip3 install -q websockets 2>/dev/null

echo "Starting Supermicro iKVM Web Console..."
echo "BMC: $BMC_HOST  User: $USERNAME"
echo ""

python3 "$SCRIPT_DIR/server.py" \
    --bmc-host "$BMC_HOST" \
    --username "$USERNAME" \
    --password "$PASSWORD"
