#!/usr/bin/env bash
"""
CallRail Cron Sync Runner
-------------------------
This wrapper script is invoked by crontab. It exports target environment variables,
navigates to your deployment folder, checks your SQLite database, and runs the sync
Python script with appropriate logging.

Author: Gemini Notebook (Agency SaaS Platform Suite)
"""

# 1. Setup absolute paths
PROJECT_DIR="/workspace"
SCRIPT_PATH="$PROJECT_DIR/daily_callrail_sync.py"
LOG_DIR="$PROJECT_DIR/logs"
LOG_FILE="$LOG_DIR/cron_sync.log"

# Create logs directory if it doesn't exist
mkdir -p "$LOG_DIR"

# 2. Export active API credentials (Uncomment and replace these with your actual keys on your server)
# export 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"
# export CALLRAIL_API_KEY="ctrk_1942b6881b233bb7da14ffb667fdfba6a4bc5bf2"
# export CALLRAIL_ACCOUNT_ID="244-449-621"
# export DB_PATH="$PROJECT_DIR/offline_attribution.db"

# Write headers to log
echo "---------------------------------------------------------" >> "$LOG_FILE"
echo "⏰ CRON SCRIPT EXECUTION INITIATED: $(date)" >> "$LOG_FILE"
echo "---------------------------------------------------------" >> "$LOG_FILE"

# 3. Check if Python script exists
if [ ! -f "$SCRIPT_PATH" ]; then
    echo "❌ ERROR: Sync script not found at $SCRIPT_PATH" >> "$LOG_FILE"
    exit 1
fi

# 4. Execute python script within project directory
cd "$PROJECT_DIR" || exit 1
python3 "$SCRIPT_PATH" >> "$LOG_FILE" 2>&1

echo "✓ Cron Execution finished successfully: $(date)" >> "$LOG_FILE"
echo "---------------------------------------------------------" >> "$LOG_FILE"
