#!/bin/bash
# Yandex Market Review Responder — daily run
# Tokens are read from files inside Python code (same pattern as WB responder)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$SCRIPT_DIR/logs"
mkdir -p "$LOG_DIR"

DATE=$(date +%Y-%m-%d_%H-%M-%S)
LOG_FILE="$LOG_DIR/run_$DATE.log"

{
    echo "=== Yandex Market Review Responder — $(date) ==="

    cd "$SCRIPT_DIR"
    PYTHONPATH="$SCRIPT_DIR" python3 -m src.entrypoints.cli_once 2>&1

    echo "=== Done at $(date) ==="
} >> "$LOG_FILE" 2>&1

# Keep only last 30 logs
ls -t "$LOG_DIR"/run_*.log 2>/dev/null | tail -n +31 | xargs rm -f 2>/dev/null || true
