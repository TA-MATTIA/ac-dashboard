"""
src/status_matrix.py — compute time spent per issue per status.

Produces two outputs:
  1. status_durations_long  — one row per (issue, status, visit)
  2. status_matrix          — one row per issue, one column per status (days)
     plus extra columns: current_status, current_assignee, accounting_due_date,
     days_to_due, top_stuck_status, max_days_in_status
"""

import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

# Exact status order for the matrix columns
STATUSES = [
    "CUSTOMER SUBMITTED",
    "PENDING INITIAL REVIEW",
    "REVIEWING",
    "FURTHER INFO REQUESTED",
    "PREPARING WP",
    "PENDING WP REVIEW",
    "REVIEWING WP",
    "PREPARING ACCOUNTS",
    "PENDING AC REVIEW",
    "ACCOUNTS READY",
    "SUBMITTED FOR SIGNATURE",
    "ACCOUNTS SIGNED",
    "ACCOUNTS FILED",
    "CT600 FILED",
    "DONE",
]

STATUS_SET = {s.upper() for s in STATUSES}


def _parse_dt(s: str) -> Optional[datetime]:
    if not s:
        return None
    try:
        s2 = s.replace("+0000", "+00:00").replace("Z", "+00:00")
        dt = datetime.fromisoformat(s2)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _days(start: datetime, end: datetime) -> float:
    return max(0.0, (end - start).total_seconds() / 86400)


def compute_status_durations(
    events: List[Dict],
    issues: List[Dict],
    run_time: Optional[datetime] = None,
) -> Tuple[List[List[Any]], List[List[Any]]]:
    """
    Returns (status_durations_long_rows, status_matrix_rows) as list-of-lists
    with headers included.
    """
    now = run_time or datetime.now(timezone.utc)

    # Build issue lookup
    issue_map: Dict[str, Dict] = {i["key"]: i for i in issues}

    # Sort events per issue by changed_at
    issue_events: Dict[str, List[Dict]] = defaultdict(list)
    for ev in events:
        issue_events[ev["issue_key"]].append(ev)
    for key in issue_events:
        issue_events[key].sort(key=lambda e: e.get("changed_at", ""))

    # ── Build durations long table ────────────────────────────────────────────
    long_rows = []

    for issue_key, evs in issue_events.items():
        issue = issue_map.get(issue_key, {})

        # Walk events: each event tells us we ENTERED to_status at changed_at
        # and EXITED from_status at changed_at
        # Build list of (status, entered_at, exited_at) spans
        spans: List[Tuple[str, datetime, datetime]] = []

        for i, ev in enumerate(evs):
            to_s = (ev.get("to_status") or "").upper()
            if to_s not in STATUS_SET:
                continue

            entered = _parse_dt(ev.get("changed_at", ""))
            if not entered:
                continue

            # Find when we exited this status = next event that has from_status == to_s
            exited = None
            for next_ev in evs[i + 1:]:
                if (next_ev.get("from_status") or "").upper() == to_s:
                    exited = _parse_dt(next_ev.get("changed_at", ""))
                    break

            # If still in this status, use now
            if exited is None:
                current_status = (issue.get("status") or "").upper()
                if current_status == to_s:
                    exited = now
                else:
                    # We left but didn't capture the exit — skip or use next event time
                    if i + 1 < len(evs):
                        exited = _parse_dt(evs[i + 1].get("changed_at", "")) or now
                    else:
                        exited = now

            days = _days(entered, exited)
            spans.append((to_s, entered, exited, days))

            long_rows.append([
                issue_key,
                to_s,
                entered.isoformat(),
                exited.isoformat(),
                round(days, 2),
            ])

        # Also add the very first status if we have created date and first event
        # shows it entering from something not in our list
        if evs:
            first_ev = evs[0]
            from_s = (first_ev.get("from_status") or "").upper()
            if from_s in STATUS_SET:
                created = _parse_dt(issue.get("created", ""))
                first_entered = _parse_dt(first_ev.get("changed_at", ""))
                if created and first_entered:
                    days = _days(created, first_entered)
                    long_rows.append([
                        issue_key,
                        from_s,
                        created.isoformat(),
                        first_entered.isoformat(),
                        round(days, 2),
                    ])

    # ── Aggregate: sum days per (issue, status) ───────────────────────────────
    # key: (issue_key, status_upper) -> total_days
    totals: Dict[Tuple[str, str], float] = defaultdict(float)
    for row in long_rows:
        totals[(row[0], row[1])] += row[4]

    # ── Build matrix rows ─────────────────────────────────────────────────────
    matrix_header = (
        ["issue_key"]
        + STATUSES
        + ["total_days", "current_status", "current_assignee",
           "accounting_due_date", "days_to_due",
           "top_stuck_status", "max_days_in_status"]
    )

    matrix_rows = [matrix_header]

    # All issues that appear in events or in issues list
    all_keys = sorted(set(list(issue_events.keys()) + [i["key"] for i in issues]))

    for key in all_keys:
        issue = issue_map.get(key, {})
        current_status = (issue.get("status") or "").upper()
        current_assignee = issue.get("assignee", "")
        due_str = issue.get("team_field", "")  # accounting due date

        # Days to due
        days_to_due = ""
        due_dt = _parse_due_date(due_str)
        if due_dt:
            days_to_due = round((due_dt - now).total_seconds() / 86400, 0)

        # Status days
        status_days = {}
        for s in STATUSES:
            status_days[s] = round(totals.get((key, s.upper()), 0.0), 2)

        total_days = round(sum(status_days.values()), 2)

        # Top stuck status
        top_status = max(status_days, key=lambda s: status_days[s]) if status_days else ""
        max_days = status_days.get(top_status, 0)

        row = (
            [key]
            + [status_days[s] for s in STATUSES]
            + [total_days, current_status, current_assignee,
               due_str, days_to_due, top_status, round(max_days, 2)]
        )
        matrix_rows.append(row)

    # Build long header
    long_header = ["issue_key", "status", "entered_at", "exited_at", "days_in_status"]
    long_rows_with_header = [long_header] + sorted(long_rows, key=lambda r: (r[0], r[2]))

    log.info(
        "Status matrix: %d issues, %d duration spans",
        len(matrix_rows) - 1,
        len(long_rows),
    )

    return long_rows_with_header, matrix_rows


def _parse_due_date(s: str) -> Optional[datetime]:
    """Try to parse accounting due date in various formats."""
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d %b %Y", "%B %d, %Y", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            dt = datetime.strptime(s.strip(), fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    # Try ISO
    try:
        return _parse_dt(s)
    except Exception:
        return None
