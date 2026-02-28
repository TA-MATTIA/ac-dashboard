"""
src/changelog_parser.py — parse raw Jira changelogs into movement_events rows.

A movement event is a status field transition. Each event gets a deterministic
event_id so the pipeline is idempotent (safe to run daily).
"""

import hashlib
import logging
from typing import Any, Dict, List

from .config import Config

log = logging.getLogger(__name__)

# Columns for the movement_events Sheet tab (order matters)
MOVEMENT_COLUMNS = [
    "event_id",
    "issue_key", "project", "issue_type", "priority",
    "created", "resolved",
    "from_status", "to_status", "changed_at", "changed_by",
    "assignee", "labels", "components", "team_field",
    "current_status", "current_assignee",
]


def parse_movement_events(
    issues: List[Dict],
    changelogs: Dict[str, List[Dict]],
    cfg: Config,
) -> List[Dict]:
    """
    For every issue, walk its changelog and emit one row per Status field change.
    Returns a list of dicts with keys = MOVEMENT_COLUMNS.
    """
    # Build a lookup from issue_key → issue dict
    issue_map = {i["key"]: i for i in issues}
    events = []

    for issue_key, cl_entries in changelogs.items():
        issue = issue_map.get(issue_key)
        if not issue:
            continue  # changelog for an issue we didn't fetch (edge case)

        for entry in cl_entries:
            author = (entry.get("author") or {}).get("displayName", "")
            changed_at = entry.get("created", "")

            for item in entry.get("items", []):
                if item.get("field") != "status":
                    continue

                from_status = item.get("fromString") or ""
                to_status = item.get("toString") or ""

                # Deterministic ID: hash of (issue_key, changed_at, from, to)
                raw_id = f"{issue_key}|{changed_at}|{from_status}|{to_status}"
                event_id = hashlib.sha256(raw_id.encode()).hexdigest()[:16]

                events.append({
                    "event_id": event_id,
                    "issue_key": issue_key,
                    "project": issue["project"],
                    "issue_type": issue["issue_type"],
                    "priority": issue["priority"],
                    "created": issue["created"],
                    "resolved": issue["resolved"],
                    "from_status": from_status,
                    "to_status": to_status,
                    "changed_at": changed_at,
                    "changed_by": author,
                    "assignee": issue["assignee"],
                    "labels": issue["labels"],
                    "components": issue["components"],
                    "team_field": issue["team_field"],
                    "current_status": issue["status"],
                    "current_assignee": issue["assignee"],
                })

    log.info("Parsed %d movement events from %d issues", len(events), len(issues))
    return events


def events_to_rows(events: List[Dict]) -> List[List[Any]]:
    """Convert list-of-dicts to list-of-lists with header row."""
    rows = [MOVEMENT_COLUMNS]
    for e in events:
        rows.append([e.get(c, "") for c in MOVEMENT_COLUMNS])
    return rows
