"""
rebuild_cache.py — rebuild local cache from existing Google Sheet data.
Run this ONCE to avoid re-fetching 7,301 issues from Jira.

Usage: python3 rebuild_cache.py
"""

import json
import logging
import os

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

from src.config import load_config
from src.cache import CACHE_DIR, ISSUES_FILE, CHANGELOGS_FILE, META_FILE, _config_hash
from src.sheets_writer import SheetsWriter

from datetime import datetime, timezone
import gspread
from google.oauth2.service_account import Credentials

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]


def main():
    cfg = load_config()

    log.info("Connecting to Google Sheet ...")
    creds = Credentials.from_service_account_file(cfg.google_service_account_file, scopes=SCOPES)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(cfg.google_sheet_id)

    # Read raw_issues_snapshot
    log.info("Reading raw_issues_snapshot ...")
    ws = sh.worksheet("raw_issues_snapshot")
    rows = ws.get_all_records()
    log.info("  Found %d issues", len(rows))

    # Convert to the same format as jira_client produces
    issues = []
    for r in rows:
        issues.append({
            "key":        r.get("key", ""),
            "project":    r.get("project", ""),
            "issue_type": r.get("issue_type", ""),
            "priority":   r.get("priority", ""),
            "summary":    r.get("summary", ""),
            "status":     r.get("status", ""),
            "assignee":   r.get("assignee", ""),
            "reporter":   r.get("reporter", ""),
            "created":    r.get("created", ""),
            "resolved":   r.get("resolved", ""),
            "labels":     r.get("labels", ""),
            "components": r.get("components", ""),
            "team_field": r.get("team_field", ""),
        })

    # Read raw_changelog_snapshot to rebuild changelogs dict
    log.info("Reading raw_changelog_snapshot ...")
    ws2 = sh.worksheet("raw_changelog_snapshot")
    cl_rows = ws2.get_all_records()
    log.info("  Found %d changelog entries", len(cl_rows))

    # Rebuild changelogs as {issue_key: [entry, ...]}
    changelogs = {}
    for r in cl_rows:
        key = r.get("issue_key", "")
        if not key:
            continue
        if key not in changelogs:
            changelogs[key] = []
        # Reconstruct a minimal changelog entry that parse_movement_events can use
        changelogs[key].append({
            "author": {"displayName": r.get("changed_by", "")},
            "created": r.get("changed_at", ""),
            "items": [{
                "field": "status",
                "fromString": r.get("from_status", ""),
                "toString": r.get("to_status", ""),
            }] if r.get("from_status") or r.get("to_status") else []
        })

    # Save cache
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(ISSUES_FILE, "w") as f:
        json.dump(issues, f)
    with open(CHANGELOGS_FILE, "w") as f:
        json.dump(changelogs, f)

    meta = {
        "last_sync": datetime.now(timezone.utc).isoformat(),
        "config_hash": _config_hash(cfg),
        "issue_count": len(issues),
        "changelog_count": len(changelogs),
    }
    with open(META_FILE, "w") as f:
        json.dump(meta, f, indent=2)

    log.info("Cache rebuilt: %d issues, %d changelogs", len(issues), len(changelogs))
    log.info("Next run of jira_sheet_sync.py will be fast (incremental only)!")


if __name__ == "__main__":
    main()
