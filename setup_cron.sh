#!/usr/bin/env bash
# setup_cron.sh — Install a daily cron job as an alternative to GitHub Actions.
# Run once: bash setup_cron.sh

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="$(which python3)"
LOG_FILE="$REPO_DIR/logs/sync.log"

mkdir -p "$REPO_DIR/logs"

# The cron line: run at 06:00 every day, log output
CRON_LINE="0 6 * * * cd $REPO_DIR && $PYTHON jira_sheet_sync.py >> $LOG_FILE 2>&1"

# Add to crontab only if not already present
( crontab -l 2>/dev/null | grep -v "jira_sheet_sync"; echo "$CRON_LINE" ) | crontab -

echo "Cron job installed:"
echo "  $CRON_LINE"
echo ""
echo "Remember to export your environment variables in ~/.bashrc or a .env file."
echo "To load a .env file in cron, change the cron command to:"
echo "  0 6 * * * cd $REPO_DIR && source .env && $PYTHON jira_sheet_sync.py >> $LOG_FILE 2>&1"
