"""
src/metrics.py — compute all KPIs from movement_events + raw issues.

KPIs:
  1. Weekly throughput (# moved into Done per week)
  2. Avg / p50 / p90 cycle time (overall + by assignee + by team)
  3. WIP by status (current snapshot)
  4. Aging WIP: stuck >7, >14, >30 days in current status
  5. Reopen rate per week
  6. Time-in-status breakdown
"""

import logging
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from .config import Config

log = logging.getLogger(__name__)

ISO_FMT = "%Y-%m-%dT%H:%M:%S.%f%z"


def _parse_dt(s: str) -> Optional[datetime]:
    if not s:
        return None
    try:
        # Jira emits "+0000" style; Python needs ±HH:MM
        s2 = s.replace("+0000", "+00:00").replace("Z", "+00:00")
        return datetime.fromisoformat(s2)
    except Exception:
        return None


def _iso_week(dt: datetime) -> str:
    return dt.strftime("%Y-W%W")


def compute_metrics(
    events: List[Dict],
    issues: List[Dict],
    cfg: Config,
) -> Dict[str, List[List[Any]]]:
    """
    Returns a dict of named metric tables, each a list-of-lists (header + rows).
    """
    done_set = set(cfg.done_statuses)
    in_progress_set = set(cfg.in_progress_statuses)
    now = datetime.now(timezone.utc)

    # ── Pre-process: per-issue sorted events ─────────────────────────────────
    issue_events: Dict[str, List[Dict]] = defaultdict(list)
    for ev in events:
        issue_events[ev["issue_key"]].append(ev)

    for key in issue_events:
        issue_events[key].sort(key=lambda e: e["changed_at"])

    # ── 1. Weekly Throughput ─────────────────────────────────────────────────
    throughput: Dict[str, int] = defaultdict(int)
    for ev in events:
        if ev["to_status"].lower() in done_set:
            dt = _parse_dt(ev["changed_at"])
            if dt:
                throughput[_iso_week(dt)] += 1

    throughput_rows = [["week", "tickets_done"]]
    for week in sorted(throughput.keys()):
        throughput_rows.append([week, throughput[week]])

    # ── 2. Cycle time & Lead time ────────────────────────────────────────────
    CycleRow = Tuple[str, str, str, float, float]  # key, assignee, team, cycle_h, lead_h

    cycle_data: List[Dict] = []
    for key, evs in issue_events.items():
        first_in_progress: Optional[datetime] = None
        first_done: Optional[datetime] = None
        created_dt: Optional[datetime] = None

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

    # By assignee
    by_assignee: Dict[str, List[Dict]] = defaultdict(list)
    by_team: Dict[str, List[Dict]] = defaultdict(list)
    for d in cycle_data:
        by_assignee[d["assignee"] or "(unassigned)"].append(d)
        by_team[d["team"] or "(no team)"].append(d)

    for a, items in sorted(by_assignee.items()):
        cycle_rows.append(_summarise(items, "assignee", f"Assignee: {a}"))
    for t, items in sorted(by_team.items()):
        cycle_rows.append(_summarise(items, "team", f"Team: {t}"))

    # ── 3. WIP by status ─────────────────────────────────────────────────────
    wip: Dict[str, int] = defaultdict(int)
    for issue in issues:
        status = issue.get("status", "(unknown)")
        if status.lower() not in done_set:
            wip[status] += 1

    wip_rows = [["status", "wip_count"]]
    for s, cnt in sorted(wip.items(), key=lambda x: -x[1]):
        wip_rows.append([s, cnt])

    # ── 4. Aging WIP ─────────────────────────────────────────────────────────
    # For each open issue, find when it entered its current status
    # (= last "to_status" event matching current status, or created if no events)
    aging_rows = [["issue_key", "current_status", "assignee", "team", "days_in_status", "bucket"]]

    for issue in issues:
        current = issue.get("status", "")
        if current.lower() in done_set:
            continue

        entered: Optional[datetime] = None
        evs = sorted(issue_events.get(issue["key"], []), key=lambda e: e["changed_at"], reverse=True)
        for ev in evs:
            if ev["to_status"].lower() == current.lower():
                entered = _parse_dt(ev["changed_at"])
                break
        if entered is None:
            entered = _parse_dt(issue.get("created", "")) or now

        days = (now - entered).days
        if days >= 30:
            bucket = ">30d"
        elif days >= 14:
            bucket = ">14d"
        elif days >= 7:
            bucket = ">7d"
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

    # ── 5. Reopen rate per week ───────────────────────────────────────────────
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

    # ── 6. Time-in-status ────────────────────────────────────────────────────
    # For each issue + status, sum up time spent
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
        "cycle_time": cycle_rows,
        "wip": wip_rows,
        "aging_wip": aging_rows,
        "reopen_rate": reopen_rows,
        "time_in_status": tis_rows,
    }
