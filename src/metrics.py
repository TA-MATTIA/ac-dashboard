"""
src/metrics.py — compute all KPIs from movement_events + raw issues.

KPIs:
  1. Weekly throughput (# moved into Done per week)
  2. Weekly submitted for signature (# moved into Submitted for Signature per week)
  3. Avg / p50 / p90 cycle time (overall + by assignee + by team)
  4. WIP by status (current snapshot, excluding Done)
  5. Aging WIP: stuck >5, >10, >30 days (excluding Done and DUE statuses)
  6. Reopen rate per week
  7. Time-in-status breakdown
"""

import logging
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from .config import Config

log = logging.getLogger(__name__)

# Statuses to exclude from stuck tickets entirely
EXCLUDE_FROM_AGING = {"due", "due date", "done"}
SUBMITTED_STATUS = "submitted for signature"


def _parse_dt(s: str) -> Optional[datetime]:
    if not s:
        return None
    try:
        s2 = s.replace("+0000", "+00:00").replace("Z", "+00:00")
        return datetime.fromisoformat(s2)
    except Exception:
        return None


def _iso_week(dt: datetime) -> str:
    return dt.strftime("%Y-W%W")


def _current_week_range():
    """Return (week_start, week_end) for the current Mon-Sun week."""
    now = datetime.now(timezone.utc)
    monday = now - __import__('datetime').timedelta(days=now.weekday())
    monday = monday.replace(hour=0, minute=0, second=0, microsecond=0)
    sunday = monday + __import__('datetime').timedelta(days=6, hours=23, minutes=59, seconds=59)
    return monday, sunday


def compute_metrics(
    events: List[Dict],
    issues: List[Dict],
    cfg: Config,
) -> Dict[str, List[List[Any]]]:
    done_set = set(cfg.done_statuses)
    in_progress_set = set(cfg.in_progress_statuses)
    now = datetime.now(timezone.utc)

    # Pre-process: per-issue sorted events
    issue_events: Dict[str, List[Dict]] = defaultdict(list)
    for ev in events:
        issue_events[ev["issue_key"]].append(ev)
    for key in issue_events:
        issue_events[key].sort(key=lambda e: e["changed_at"])

    # ── 1. Weekly Throughput ──────────────────────────────────────────────────
    throughput: Dict[str, int] = defaultdict(int)
    for ev in events:
        if ev["to_status"].lower() in done_set:
            dt = _parse_dt(ev["changed_at"])
            if dt:
                throughput[_iso_week(dt)] += 1

    throughput_rows = [["week", "tickets_done"]]
    for week in sorted(throughput.keys()):
        throughput_rows.append([week, throughput[week]])

    # ── 2. Weekly Submitted for Signature ────────────────────────────────────
    submitted_weekly: Dict[str, int] = defaultdict(int)
    for ev in events:
        if ev["to_status"].lower() == SUBMITTED_STATUS:
            dt = _parse_dt(ev["changed_at"])
            if dt:
                submitted_weekly[_iso_week(dt)] += 1

    submitted_rows = [["week", "submitted_for_signature"]]
    for week in sorted(submitted_weekly.keys()):
        submitted_rows.append([week, submitted_weekly[week]])

    # ── 3. Cycle time & Lead time ─────────────────────────────────────────────
    cycle_data: List[Dict] = []
    for key, evs in issue_events.items():
        first_in_progress: Optional[datetime] = None
        first_done: Optional[datetime] = None
        issue = next((i for i in issues if i["key"] == key), {})
        created_dt = _parse_dt(issue.get("created", ""))

        for ev in evs:
            ts = _parse_dt(ev["changed_at"])
            if not ts:
                continue
            if ev["to_status"].lower() in in_progress_set and first_in_progress is None:
                first_in_progress = ts
            if ev["to_status"].lower() in done_set and first_done is None:
                first_done = ts

        if first_done is None:
            continue

        cycle_h = (first_done - first_in_progress).total_seconds() / 3600 if first_in_progress else None
        lead_h = (first_done - created_dt).total_seconds() / 3600 if created_dt else None

        cycle_data.append({
            "issue_key": key,
            "assignee": issue.get("assignee", ""),
            "team": issue.get("team_field", ""),
            "cycle_hours": cycle_h,
            "lead_hours": lead_h,
        })

    def _percentile(data: List[float], p: int) -> float:
        if not data:
            return 0.0
        s = sorted(data)
        idx = int(len(s) * p / 100)
        return round(s[min(idx, len(s) - 1)], 2)

    def _summarise(items: List[Dict], group_key: str, group_val: str) -> List[Any]:
        c_hours = [d["cycle_hours"] for d in items if d["cycle_hours"] is not None]
        l_hours = [d["lead_hours"] for d in items if d["lead_hours"] is not None]
        return [
            group_val,
            len(items),
            round(statistics.mean(c_hours), 2) if c_hours else "",
            _percentile(c_hours, 50),
            _percentile(c_hours, 90),
            round(statistics.mean(l_hours), 2) if l_hours else "",
            _percentile(l_hours, 50),
            _percentile(l_hours, 90),
        ]

    cycle_header = [
        "group", "count",
        "cycle_avg_h", "cycle_p50_h", "cycle_p90_h",
        "lead_avg_h", "lead_p50_h", "lead_p90_h",
    ]
    cycle_rows = [cycle_header, _summarise(cycle_data, "", "Overall")]

    by_assignee: Dict[str, List[Dict]] = defaultdict(list)
    by_team: Dict[str, List[Dict]] = defaultdict(list)
    for d in cycle_data:
        by_assignee[d["assignee"] or "(unassigned)"].append(d)
        by_team[d["team"] or "(no team)"].append(d)

    for a, items in sorted(by_assignee.items()):
        cycle_rows.append(_summarise(items, "assignee", f"Assignee: {a}"))
    for t, items in sorted(by_team.items()):
        cycle_rows.append(_summarise(items, "team", f"Team: {t}"))

    # ── 4. WIP by status (exclude Done) ──────────────────────────────────────
    wip: Dict[str, int] = defaultdict(int)
    for issue in issues:
        status = issue.get("status", "(unknown)")
        if status.lower() not in done_set:
            wip[status] += 1

    wip_rows = [["status", "wip_count"]]
    for s, cnt in sorted(wip.items(), key=lambda x: -x[1]):
        wip_rows.append([s, cnt])

    # ── 5. Aging WIP (exclude Done AND DUE statuses) ─────────────────────────
    aging_rows = [["issue_key", "current_status", "assignee", "team_field", "days_in_status", "bucket"]]

    for issue in issues:
        current = issue.get("status", "")
        current_lower = current.lower()

        # Skip Done statuses AND any status containing "due"
        if current_lower in done_set:
            continue
        if any(excl in current_lower for excl in EXCLUDE_FROM_AGING):
            continue

        entered: Optional[datetime] = None
        evs = sorted(issue_events.get(issue["key"], []), key=lambda e: e["changed_at"], reverse=True)
        for ev in evs:
            if ev["to_status"].lower() == current_lower:
                entered = _parse_dt(ev["changed_at"])
                break
        if entered is None:
            entered = _parse_dt(issue.get("created", "")) or now

        days = (now - entered).days
        if days >= 30:
            bucket = ">30d"
        elif days >= 10:
            bucket = ">10d"
        elif days >= 5:
            bucket = ">5d"
        else:
            continue  # not aging

        aging_rows.append([
            issue["key"],
            current,
            issue.get("assignee", ""),
            issue.get("team_field", ""),
            days,
            bucket,
        ])

    # ── 6. Reopen rate per week ───────────────────────────────────────────────
    reopens: Dict[str, int] = defaultdict(int)
    for ev in events:
        if ev["from_status"].lower() in done_set and ev["to_status"].lower() not in done_set:
            dt = _parse_dt(ev["changed_at"])
            if dt:
                reopens[_iso_week(dt)] += 1

    reopen_rows = [["week", "tickets_done", "reopens", "reopen_rate_pct"]]
    all_weeks = sorted(set(list(throughput.keys()) + list(reopens.keys())))
    for week in all_weeks:
        done = throughput.get(week, 0)
        r = reopens.get(week, 0)
        rate = round(r / done * 100, 1) if done else ""
        reopen_rows.append([week, done, r, rate])

    # ── 7. Time-in-status ─────────────────────────────────────────────────────
    status_time: Dict[str, List[float]] = defaultdict(list)
    for key, evs in issue_events.items():
        sorted_evs = sorted(evs, key=lambda e: e["changed_at"])
        for i, ev in enumerate(sorted_evs):
            enter = _parse_dt(ev["changed_at"])
            if i + 1 < len(sorted_evs):
                leave = _parse_dt(sorted_evs[i + 1]["changed_at"])
            else:
                leave = now
            if enter and leave and leave > enter:
                hours = (leave - enter).total_seconds() / 3600
                status_time[ev["to_status"]].append(hours)

    tis_rows = [["status", "count", "avg_hours", "p50_hours", "p90_hours"]]
    for status, hours_list in sorted(status_time.items()):
        tis_rows.append([
            status,
            len(hours_list),
            round(statistics.mean(hours_list), 2),
            _percentile(hours_list, 50),
            _percentile(hours_list, 90),
        ])

    return {
        "throughput": throughput_rows,
        "submitted_for_signature": submitted_rows,
        "cycle_time": cycle_rows,
        "wip": wip_rows,
        "aging_wip": aging_rows,
        "reopen_rate": reopen_rows,
        "time_in_status": tis_rows,
    }
