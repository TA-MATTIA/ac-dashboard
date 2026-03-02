"""
src/dashboard.py — generate a self-contained HTML dashboard.
Includes: KPIs, stuck tickets with filters, full status matrix with filters.
All data is embedded as JSON so the file works on GitHub Pages with no backend.
"""

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

log = logging.getLogger(__name__)

STATUSES = [
    "CUSTOMER SUBMITTED", "PENDING INITIAL REVIEW", "REVIEWING",
    "FURTHER INFO REQUESTED", "PREPARING WP", "PENDING WP REVIEW",
    "REVIEWING WP", "PREPARING ACCOUNTS", "PENDING AC REVIEW",
    "ACCOUNTS READY", "SUBMITTED FOR SIGNATURE", "ACCOUNTS SIGNED",
    "ACCOUNTS FILED", "CT600 FILED", "DONE",
]


def _get_week_label(offset=0):
    dt = datetime.now(timezone.utc) + timedelta(weeks=offset)
    return dt.strftime("%Y-W%W")


def generate_dashboard(
    issues: List[Dict],
    events: List[Dict],
    metrics: Dict[str, List[List[Any]]],
    output_path: str = "dashboard/index.html",
):
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    def _extract(key):
        table = metrics.get(key, [])
        if not table or len(table) < 2:
            return []
        header = table[0]
        return [dict(zip(header, row)) for row in table[1:]]

    throughput  = _extract("throughput")
    submitted   = _extract("submitted_for_signature")
    cycle_time  = _extract("cycle_time")
    wip         = _extract("wip")
    aging       = _extract("aging_wip")
    reopen      = _extract("reopen_rate")
    tis         = _extract("time_in_status")

    # KPIs
    this_week = _get_week_label(0)
    last_week = _get_week_label(-1)

    def _week_val(table, week, col):
        row = next((r for r in table if r.get("week") == week), {})
        return int(row.get(col, 0))

    sig_this  = _week_val(submitted, this_week, "submitted_for_signature")
    sig_last  = _week_val(submitted, last_week, "submitted_for_signature")
    sig_delta = sig_this - sig_last
    total_wip = sum(int(r.get("wip_count", 0)) for r in wip)

    overall_ct  = next((r for r in cycle_time if r.get("group") == "Overall"), {})
    cycle_avg   = overall_ct.get("cycle_avg_h", "")
    cycle_avg_d = round(float(cycle_avg) / 24, 1) if cycle_avg else "—"
    cycle_p50_d = round(float(overall_ct.get("cycle_p50_h", 0)) / 24, 1) if overall_ct.get("cycle_p50_h") else "—"
    cycle_p90_d = round(float(overall_ct.get("cycle_p90_h", 0)) / 24, 1) if overall_ct.get("cycle_p90_h") else "—"
    reopen_rate = reopen[-1].get("reopen_rate_pct", "—") if reopen else "—"

    stuck_5  = len(aging)
    stuck_10 = sum(1 for r in aging if r.get("bucket") in (">10d", ">30d"))
    stuck_30 = sum(1 for r in aging if r.get("bucket") == ">30d")

    last_sync    = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    total_issues = len(issues)

    # Build issue lookup for matrix
    issue_map = {i["key"]: i for i in issues}

    # Build matrix rows from issues + aging data
    matrix_data = []
    for issue in issues:
        key = issue["key"]
        status = (issue.get("status") or "").upper()
        assignee = issue.get("assignee", "") or ""
        due = issue.get("team_field", "") or ""
        summary = issue.get("summary", "") or ""

        # Days to due
        days_to_due = None
        if due:
            try:
                due_clean = due.replace("Sept", "Sep")
                for fmt in ("%Y-%m-%d", "%d %b %Y", "%d/%m/%Y"):
                    try:
                        due_dt = datetime.strptime(due_clean, fmt).replace(tzinfo=timezone.utc)
                        days_to_due = int((due_dt - datetime.now(timezone.utc)).days)
                        break
                    except ValueError:
                        continue
            except Exception:
                pass

        matrix_data.append({
            "key": key,
            "summary": summary[:50],
            "status": status,
            "assignee": assignee,
            "due": due,
            "days_to_due": days_to_due,
        })

    # Enrich aging with summary
    aging_enriched = []
    for r in aging:
        issue = issue_map.get(r.get("issue_key", ""), {})
        aging_enriched.append({
            **r,
            "summary": (issue.get("summary", "") or "")[:50],
            "due": issue.get("team_field", "") or "",
        })

    # Recent events
    recent_events = sorted(events, key=lambda e: e.get("changed_at", ""), reverse=True)[:100]

    sig_trend = ("↑ " + str(abs(sig_delta)) + " vs last week") if sig_delta >= 0 else ("↓ " + str(abs(sig_delta)) + " vs last week")
    sig_trend_class = "trend-up" if sig_delta >= 0 else "trend-down"

    # JSON payloads
    submitted_json  = json.dumps(submitted[-16:])
    wip_json        = json.dumps(wip[:15])
    aging_json      = json.dumps(sorted(aging_enriched, key=lambda r: int(r.get("days_in_status", 0)), reverse=True))
    reopen_json     = json.dumps(reopen[-10:])
    tis_json        = json.dumps(sorted(tis, key=lambda r: float(r.get("avg_hours", 0)), reverse=True)[:12])
    matrix_json     = json.dumps(matrix_data)
    events_json     = json.dumps([{
        "key": e.get("issue_key",""), "from": e.get("from_status",""),
        "to": e.get("to_status",""), "at": e.get("changed_at","")[:16].replace("T"," "),
        "by": e.get("changed_by",""), "assignee": e.get("assignee",""),
        "due": e.get("team_field",""),
    } for e in recent_events])

    # Get unique assignees and statuses for filter dropdowns
    assignees = sorted(set(i.get("assignee","") for i in issues if i.get("assignee","")))
    statuses_active = sorted(set((i.get("status","") or "").upper() for i in issues if i.get("status","")))
    assignees_json = json.dumps(assignees)
    statuses_json = json.dumps(statuses_active)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AC — Accounting Dashboard</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=Instrument+Serif:ital@0;1&family=DM+Sans:wght@300;400;500;600&display=swap');
:root{{
  --bg:#0f1117;--surface:#181c27;--surface2:#1e2334;--border:#2a3045;
  --accent:#4f9cf9;--accent2:#f97b4f;--accent3:#4fca8f;--accent4:#c97bf9;
  --text:#e8ecf4;--muted:#6b7699;--warn:#f9c44f;--danger:#f97b4f;
}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:var(--bg);color:var(--text);font-family:'DM Sans',sans-serif;font-size:13px;min-height:100vh}}
.header{{padding:20px 28px 14px;border-bottom:1px solid var(--border);display:flex;align-items:baseline;gap:16px;flex-wrap:wrap}}
.header h1{{font-family:'Instrument Serif',serif;font-size:22px;font-weight:400}}
.meta{{font-family:'DM Mono',monospace;font-size:11px;color:var(--muted)}}
.tab-bar{{display:flex;gap:2px;padding:10px 28px 0;border-bottom:1px solid var(--border);overflow-x:auto}}
.tab{{padding:8px 16px;font-size:12px;color:var(--muted);cursor:pointer;border-bottom:2px solid transparent;white-space:nowrap;transition:all .15s;font-family:'DM Mono',monospace;letter-spacing:.03em}}
.tab:hover{{color:var(--text)}} .tab.active{{color:var(--accent);border-bottom-color:var(--accent)}}
.content{{padding:20px 28px;display:none}} .content.active{{display:block;animation:fadeIn .2s ease}}
@keyframes fadeIn{{from{{opacity:0;transform:translateY(4px)}}to{{opacity:1;transform:none}}}}

/* KPI cards */
.kpi-row{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:16px}}
.kpi{{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:14px 16px}}
.kpi .label{{font-size:10px;color:var(--muted);font-family:'DM Mono',monospace;letter-spacing:.05em;margin-bottom:6px;text-transform:uppercase}}
.kpi .value{{font-size:28px;font-family:'Instrument Serif',serif;line-height:1}}
.kpi .sub{{font-size:11px;color:var(--muted);margin-top:4px}}
.trend-up{{color:var(--accent3)}} .trend-down{{color:var(--danger)}}

/* Filters */
.filter-bar{{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:14px;padding:12px 14px;background:var(--surface);border:1px solid var(--border);border-radius:8px}}
.filter-bar label{{font-size:10px;color:var(--muted);font-family:'DM Mono',monospace;text-transform:uppercase;letter-spacing:.05em;white-space:nowrap}}
.filter-bar select, .filter-bar input{{background:var(--surface2);border:1px solid var(--border);color:var(--text);font-family:'DM Mono',monospace;font-size:11px;padding:5px 8px;border-radius:5px;outline:none;cursor:pointer}}
.filter-bar select:focus, .filter-bar input:focus{{border-color:var(--accent)}}
.filter-bar input{{width:180px}}
.btn{{background:var(--surface2);border:1px solid var(--border);color:var(--muted);font-family:'DM Mono',monospace;font-size:11px;padding:5px 12px;border-radius:5px;cursor:pointer;transition:all .15s}}
.btn:hover{{color:var(--text);border-color:var(--accent)}}
.btn.active{{background:rgba(79,156,249,.15);color:var(--accent);border-color:var(--accent)}}
.result-count{{font-size:11px;color:var(--muted);font-family:'DM Mono',monospace;margin-left:auto}}

/* Cards and grids */
.grid2{{display:grid;grid-template-columns:1.6fr 1fr;gap:10px;margin-bottom:10px}}
.grid3{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:10px}}
.card{{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:16px 18px}}
.card h3{{font-size:10px;color:var(--muted);font-family:'DM Mono',monospace;letter-spacing:.06em;text-transform:uppercase;margin-bottom:14px}}

/* Bar chart */
.bar-chart{{display:flex;flex-direction:column;gap:5px}}
.bar-row{{display:flex;align-items:center;gap:8px}}
.bar-label{{width:155px;font-size:10px;color:var(--muted);text-align:right;flex-shrink:0;font-family:'DM Mono',monospace;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.bar-track{{flex:1;background:var(--surface2);border-radius:2px;height:17px;overflow:hidden}}
.bar-fill{{height:100%;border-radius:2px;display:flex;align-items:center;padding-left:6px;font-size:10px;font-family:'DM Mono',monospace;color:rgba(255,255,255,.8);white-space:nowrap;transition:width .3s ease}}
.bar-val{{width:38px;font-size:10px;font-family:'DM Mono',monospace;color:var(--muted);text-align:right;flex-shrink:0}}

/* Spark */
.spark-wrap{{display:flex;align-items:flex-end;gap:4px;height:80px}}
.spark-col{{display:flex;flex-direction:column;align-items:center;gap:2px;flex:1}}
.spark-bar{{width:100%;border-radius:3px 3px 0 0;opacity:.8;transition:opacity .2s}}
.spark-bar:hover{{opacity:1}}
.spark-label{{font-size:9px;color:var(--muted);font-family:'DM Mono',monospace}}
.spark-val{{font-size:9px;font-family:'DM Mono',monospace}}

/* Aging boxes */
.aging-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:14px}}
.aging-box{{background:var(--surface2);border-radius:6px;padding:12px;text-align:center}}
.aging-num{{font-size:32px;font-family:'Instrument Serif',serif;line-height:1;margin-bottom:3px}}
.aging-lbl{{font-size:10px;color:var(--muted);font-family:'DM Mono',monospace}}

/* Table */
.tbl-wrap{{overflow-x:auto;border-radius:6px;border:1px solid var(--border)}}
table{{width:100%;border-collapse:collapse}}
th{{font-size:10px;color:var(--muted);font-family:'DM Mono',monospace;text-transform:uppercase;letter-spacing:.05em;padding:8px 10px;text-align:left;border-bottom:1px solid var(--border);white-space:nowrap;cursor:pointer;user-select:none;background:var(--surface)}}
th:hover{{color:var(--text)}}
th.sorted-asc::after{{content:" ↑";color:var(--accent)}}
th.sorted-desc::after{{content:" ↓";color:var(--accent)}}
td{{padding:7px 10px;font-size:11px;border-bottom:1px solid #1a1f2e;font-family:'DM Mono',monospace;white-space:nowrap}}
tr:last-child td{{border-bottom:none}}
tr:hover td{{background:var(--surface2)}}
.badge{{display:inline-block;padding:2px 7px;border-radius:10px;font-size:10px;font-family:'DM Mono',monospace;white-space:nowrap}}
.empty{{text-align:center;padding:40px;color:var(--muted);font-family:'DM Mono',monospace}}
.note{{background:rgba(79,156,249,.08);border:1px solid rgba(79,156,249,.2);border-radius:6px;padding:8px 12px;font-size:11px;color:var(--accent);margin-bottom:12px}}

/* Matrix specific */
.matrix-cell{{text-align:right;font-variant-numeric:tabular-nums}}
.matrix-cell.hot{{color:var(--danger);font-weight:500}}
.matrix-cell.warm{{color:var(--warn)}}
.matrix-cell.ok{{color:var(--accent3)}}

@media(max-width:900px){{.kpi-row{{grid-template-columns:repeat(2,1fr)}}.grid2,.grid3{{grid-template-columns:1fr}}}}
</style>
</head>
<body>
<div class="header">
  <h1>AC — Accounting Dashboard</h1>
  <span class="meta">last sync: {last_sync} &nbsp;·&nbsp; {total_issues:,} companies</span>
</div>
<div class="tab-bar">
  <div class="tab active" onclick="show('overview',this)">📊 Overview</div>
  <div class="tab" onclick="show('stuck',this)">🚨 Stuck Tickets</div>
  <div class="tab" onclick="show('matrix',this)">📋 Status Matrix</div>
  <div class="tab" onclick="show('activity',this)">⚡ Recent Activity</div>
</div>

<!-- OVERVIEW -->
<div class="content active" id="overview">
  <div class="kpi-row">
    <div class="kpi">
      <div class="label">This Week — Submitted for Signature</div>
      <div class="value" style="color:var(--accent3)">{sig_this}</div>
      <div class="sub {sig_trend_class}">{sig_trend}</div>
    </div>
    <div class="kpi">
      <div class="label">Avg Cycle Time</div>
      <div class="value" style="color:var(--accent)">{cycle_avg_d}<span style="font-size:14px;color:var(--muted)"> days</span></div>
      <div class="sub">p50: {cycle_p50_d}d &nbsp;·&nbsp; p90: {cycle_p90_d}d</div>
    </div>
    <div class="kpi">
      <div class="label">Current WIP</div>
      <div class="value">{total_wip}</div>
      <div class="sub">open accounts right now</div>
    </div>
    <div class="kpi">
      <div class="label">Reopen Rate</div>
      <div class="value" style="color:var(--warn)">{reopen_rate}<span style="font-size:14px">%</span></div>
      <div class="sub">moved back from Done</div>
    </div>
  </div>
  <div class="grid2">
    <div class="card"><h3>Weekly — Submitted for Signature</h3><div class="spark-wrap" id="submitted-chart"></div></div>
    <div class="card"><h3>WIP by Status</h3><div class="bar-chart" id="wip-chart"></div></div>
  </div>
  <div class="grid2">
    <div class="card">
      <h3>Stuck Accounts Summary (excl. Done &amp; Due)</h3>
      <div class="aging-grid">
        <div class="aging-box"><div class="aging-num" style="color:var(--warn)">{stuck_5}</div><div class="aging-lbl">stuck &gt;5 days</div></div>
        <div class="aging-box"><div class="aging-num" style="color:var(--danger)">{stuck_10}</div><div class="aging-lbl">stuck &gt;10 days</div></div>
        <div class="aging-box"><div class="aging-num" style="color:#ff4444">{stuck_30}</div><div class="aging-lbl">stuck &gt;30 days</div></div>
      </div>
      <div class="bar-chart" id="stuck-by-status"></div>
    </div>
    <div class="card"><h3>Reopen Rate per Week</h3><div class="bar-chart" id="reopen-chart"></div></div>
  </div>
</div>

<!-- STUCK TICKETS -->
<div class="content" id="stuck">
  <div class="note">🚨 Accounts stuck in the same status for 5+ days. Done and Due statuses excluded.</div>
  <div class="filter-bar">
    <label>Search</label>
    <input type="text" id="stuck-search" placeholder="ticket, name, assignee..." oninput="filterStuck()">
    <label>Status</label>
    <select id="stuck-status" onchange="filterStuck()"><option value="">All statuses</option></select>
    <label>Assignee</label>
    <select id="stuck-assignee" onchange="filterStuck()"><option value="">All assignees</option></select>
    <label>Bucket</label>
    <button class="btn active" id="btn-5" onclick="toggleBucket('>5d',this)">5+ days</button>
    <button class="btn" id="btn-10" onclick="toggleBucket('>10d',this)">10+ days</button>
    <button class="btn" id="btn-30" onclick="toggleBucket('>30d',this)">30+ days</button>
    <button class="btn" onclick="clearStuckFilters()" style="margin-left:4px">Clear</button>
    <span class="result-count" id="stuck-count"></span>
  </div>
  <div class="tbl-wrap">
    <table id="stuck-table">
      <thead><tr>
        <th onclick="sortTable('stuck-table',0)">Ticket</th>
        <th onclick="sortTable('stuck-table',1)">Company</th>
        <th onclick="sortTable('stuck-table',2)">Status</th>
        <th onclick="sortTable('stuck-table',3)">Assignee</th>
        <th onclick="sortTable('stuck-table',4)">Days Stuck</th>
        <th onclick="sortTable('stuck-table',5)">Due Date</th>
        <th onclick="sortTable('stuck-table',6)">Bucket</th>
      </tr></thead>
      <tbody id="stuck-body"></tbody>
    </table>
  </div>
</div>

<!-- STATUS MATRIX -->
<div class="content" id="matrix">
  <div class="note">📋 All accounts with their current status. Use filters to find specific accounts or stages.</div>
  <div class="filter-bar">
    <label>Search</label>
    <input type="text" id="matrix-search" placeholder="ticket, company name..." oninput="filterMatrix()">
    <label>Status</label>
    <select id="matrix-status" onchange="filterMatrix()"><option value="">All statuses</option></select>
    <label>Assignee</label>
    <select id="matrix-assignee" onchange="filterMatrix()"><option value="">All assignees</option></select>
    <label>Overdue</label>
    <button class="btn" id="btn-overdue" onclick="toggleOverdue(this)">Overdue only</button>
    <button class="btn" onclick="clearMatrixFilters()">Clear</button>
    <span class="result-count" id="matrix-count"></span>
  </div>
  <div class="tbl-wrap">
    <table id="matrix-table">
      <thead><tr>
        <th onclick="sortTable('matrix-table',0)">Ticket</th>
        <th onclick="sortTable('matrix-table',1)">Company</th>
        <th onclick="sortTable('matrix-table',2)">Current Status</th>
        <th onclick="sortTable('matrix-table',3)">Assignee</th>
        <th onclick="sortTable('matrix-table',4)">Due Date</th>
        <th onclick="sortTable('matrix-table',5)">Days to Due</th>
      </tr></thead>
      <tbody id="matrix-body"></tbody>
    </table>
  </div>
</div>

<!-- RECENT ACTIVITY -->
<div class="content" id="activity">
  <div class="filter-bar">
    <label>Search</label>
    <input type="text" id="activity-search" placeholder="ticket, status, assignee..." oninput="filterActivity()">
    <label>Status</label>
    <select id="activity-status" onchange="filterActivity()"><option value="">All statuses</option></select>
    <label>Assignee</label>
    <select id="activity-assignee" onchange="filterActivity()"><option value="">All assignees</option></select>
    <button class="btn" onclick="clearActivityFilters()">Clear</button>
    <span class="result-count" id="activity-count"></span>
  </div>
  <div class="tbl-wrap">
    <table id="activity-table">
      <thead><tr>
        <th onclick="sortTable('activity-table',0)">Ticket</th>
        <th onclick="sortTable('activity-table',1)">From</th>
        <th onclick="sortTable('activity-table',2)">To</th>
        <th onclick="sortTable('activity-table',3)">When</th>
        <th onclick="sortTable('activity-table',4)">By</th>
        <th onclick="sortTable('activity-table',5)">Assignee</th>
        <th onclick="sortTable('activity-table',6)">Due Date</th>
      </tr></thead>
      <tbody id="activity-body"></tbody>
    </table>
  </div>
</div>

<script>
const SUBMITTED  = {submitted_json};
const WIP        = {wip_json};
const AGING      = {aging_json};
const REOPEN     = {reopen_json};
const TIS        = {tis_json};
const MATRIX     = {matrix_json};
const EVENTS     = {events_json};
const ASSIGNEES  = {assignees_json};
const STATUSES_L = {statuses_json};

const COLORS = ['#4f9cf9','#c97bf9','#f9c44f','#4fca8f','#f97b4f','#4fc9f9','#f94f9c','#9cf94f','#f9a44f','#4f4ff9'];

// ── Tab switching ──────────────────────────────────────────────────────────
function show(id, el) {{
  document.querySelectorAll('.content').forEach(e => e.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(e => e.classList.remove('active'));
  document.getElementById(id).classList.add('active');
  el.classList.add('active');
}}

// ── Status badge colour ────────────────────────────────────────────────────
function statusColor(s) {{
  s = (s||'').toUpperCase();
  if (s.includes('FILED') || s.includes('CT600') || s.includes('DONE')) return '#4fca8f';
  if (s.includes('SIGNED')) return '#4fca8f';
  if (s.includes('FURTHER INFO') || s.includes('PENDING')) return '#f9c44f';
  if (s.includes('REVIEW')) return '#c97bf9';
  if (s.includes('SUBMITTED')) return '#4f9cf9';
  if (s.includes('PREPARING')) return '#4fc9f9';
  if (s.includes('CUSTOMER')) return '#6b7699';
  return '#6b7699';
}}
function badge(text, color) {{
  return `<span class="badge" style="background:${{color}}22;color:${{color}}">${{text||'—'}}</span>`;
}}
function daysColor(d) {{
  if (d === null || d === undefined || d === '') return 'var(--muted)';
  d = +d;
  if (d < 0) return '#ff4444';
  if (d < 14) return 'var(--warn)';
  return 'var(--accent3)';
}}

// ── Sort table ─────────────────────────────────────────────────────────────
let sortState = {{}};
function sortTable(tableId, colIdx) {{
  const table = document.getElementById(tableId);
  const tbody = table.querySelector('tbody');
  const rows = Array.from(tbody.querySelectorAll('tr:not(.hidden-row)'));
  const key = tableId + '_' + colIdx;
  const asc = sortState[key] !== true;
  sortState[key] = asc;

  table.querySelectorAll('th').forEach((th,i) => {{
    th.classList.remove('sorted-asc','sorted-desc');
    if (i === colIdx) th.classList.add(asc ? 'sorted-asc' : 'sorted-desc');
  }});

  rows.sort((a, b) => {{
    const av = a.cells[colIdx]?.dataset?.val ?? a.cells[colIdx]?.textContent ?? '';
    const bv = b.cells[colIdx]?.dataset?.val ?? b.cells[colIdx]?.textContent ?? '';
    const an = parseFloat(av), bn = parseFloat(bv);
    if (!isNaN(an) && !isNaN(bn)) return asc ? an - bn : bn - an;
    return asc ? av.localeCompare(bv) : bv.localeCompare(av);
  }});
  rows.forEach(r => tbody.appendChild(r));
}}

// ── Populate dropdowns ─────────────────────────────────────────────────────
function populateSelect(id, values) {{
  const sel = document.getElementById(id);
  if (!sel) return;
  values.forEach(v => {{
    const o = document.createElement('option');
    o.value = v; o.textContent = v;
    sel.appendChild(o);
  }});
}}

// ── Stuck tickets ──────────────────────────────────────────────────────────
let stuckBucket = '>5d';
function toggleBucket(b, el) {{
  stuckBucket = b;
  document.querySelectorAll('[id^="btn-"]').forEach(btn => btn.classList.remove('active'));
  el.classList.add('active');
  filterStuck();
}}
function clearStuckFilters() {{
  document.getElementById('stuck-search').value = '';
  document.getElementById('stuck-status').value = '';
  document.getElementById('stuck-assignee').value = '';
  stuckBucket = '>5d';
  document.getElementById('btn-5').classList.add('active');
  document.getElementById('btn-10').classList.remove('active');
  document.getElementById('btn-30').classList.remove('active');
  filterStuck();
}}
function filterStuck() {{
  const search = document.getElementById('stuck-search').value.toLowerCase();
  const status = document.getElementById('stuck-status').value.toUpperCase();
  const assignee = document.getElementById('stuck-assignee').value;
  const buckets = stuckBucket === '>30d' ? ['>30d'] :
                  stuckBucket === '>10d' ? ['>10d','>30d'] :
                  ['>5d','>10d','>30d'];

  let count = 0;
  document.querySelectorAll('#stuck-body tr').forEach(row => {{
    const show =
      (!search || row.textContent.toLowerCase().includes(search)) &&
      (!status || (row.dataset.status||'').toUpperCase() === status) &&
      (!assignee || row.dataset.assignee === assignee) &&
      buckets.includes(row.dataset.bucket);
    row.style.display = show ? '' : 'none';
    if (show) count++;
  }});
  document.getElementById('stuck-count').textContent = count + ' accounts';
}}

function renderStuck() {{
  const body = document.getElementById('stuck-body');
  const stuckStatuses = new Set();
  const stuckAssignees = new Set();

  if (!AGING.length) {{
    body.innerHTML = '<tr><td colspan="7" class="empty">No stuck accounts 🎉</td></tr>';
    return;
  }}

  AGING.forEach(r => {{
    const days = +r.days_in_status;
    const dc = days>=30?'#ff4444':days>=10?'var(--danger)':'var(--warn)';
    const sc = statusColor(r.current_status);
    const s = (r.current_status||'').toUpperCase();
    const a = r.assignee||'';
    stuckStatuses.add(s);
    if (a) stuckAssignees.add(a);
    body.innerHTML += `<tr data-status="${{s}}" data-assignee="${{a}}" data-bucket="${{r.bucket||''}}">
      <td><a href="https://getground.atlassian.net/browse/${{r.issue_key}}" target="_blank" style="color:var(--accent);text-decoration:none">${{r.issue_key}}</a></td>
      <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis" title="${{r.summary||''}}">${{r.summary||'—'}}</td>
      <td>${{badge(r.current_status, sc)}}</td>
      <td>${{a||'—'}}</td>
      <td data-val="${{days}}" style="color:${{dc}};font-weight:500">${{days}}d</td>
      <td>${{r.due||'—'}}</td>
      <td>${{badge(r.bucket, dc)}}</td>
    </tr>`;
  }});

  populateSelect('stuck-status', [...stuckStatuses].sort());
  populateSelect('stuck-assignee', [...stuckAssignees].sort());
  filterStuck();
}}

// ── Status matrix ──────────────────────────────────────────────────────────
let overdueOnly = false;
function toggleOverdue(el) {{
  overdueOnly = !overdueOnly;
  el.classList.toggle('active', overdueOnly);
  filterMatrix();
}}
function clearMatrixFilters() {{
  document.getElementById('matrix-search').value = '';
  document.getElementById('matrix-status').value = '';
  document.getElementById('matrix-assignee').value = '';
  overdueOnly = false;
  document.getElementById('btn-overdue').classList.remove('active');
  filterMatrix();
}}
function filterMatrix() {{
  const search = document.getElementById('matrix-search').value.toLowerCase();
  const status = document.getElementById('matrix-status').value.toUpperCase();
  const assignee = document.getElementById('matrix-assignee').value;

  let count = 0;
  document.querySelectorAll('#matrix-body tr').forEach(row => {{
    const dtd = parseFloat(row.dataset.daystoDue);
    const show =
      (!search || row.textContent.toLowerCase().includes(search)) &&
      (!status || (row.dataset.status||'').toUpperCase() === status) &&
      (!assignee || row.dataset.assignee === assignee) &&
      (!overdueOnly || (!isNaN(dtd) && dtd < 0));
    row.style.display = show ? '' : 'none';
    if (show) count++;
  }});
  document.getElementById('matrix-count').textContent = count + ' accounts';
}}

function renderMatrix() {{
  const body = document.getElementById('matrix-body');
  const matrixStatuses = new Set();
  const matrixAssignees = new Set();

  if (!MATRIX.length) {{
    body.innerHTML = '<tr><td colspan="6" class="empty">No data</td></tr>';
    return;
  }}

  MATRIX.forEach(r => {{
    const s = (r.status||'').toUpperCase();
    const a = r.assignee||'';
    const dtd = r.days_to_due;
    const dc = daysColor(dtd);
    const sc = statusColor(s);
    matrixStatuses.add(s);
    if (a) matrixAssignees.add(a);

    body.innerHTML += `<tr data-status="${{s}}" data-assignee="${{a}}" data-days-to-due="${{dtd ?? ''}}">
      <td><a href="https://getground.atlassian.net/browse/${{r.key}}" target="_blank" style="color:var(--accent);text-decoration:none">${{r.key}}</a></td>
      <td style="max-width:220px;overflow:hidden;text-overflow:ellipsis" title="${{r.summary||''}}">${{r.summary||'—'}}</td>
      <td>${{badge(s, sc)}}</td>
      <td>${{a||'—'}}</td>
      <td>${{r.due||'—'}}</td>
      <td data-val="${{dtd ?? 9999}}" style="color:${{dc}};font-weight:${{dtd !== null && dtd < 0 ? '600' : '400'}}">${{dtd !== null && dtd !== undefined ? dtd + 'd' : '—'}}</td>
    </tr>`;
  }});

  populateSelect('matrix-status', [...matrixStatuses].sort());
  populateSelect('matrix-assignee', [...matrixAssignees].sort());
  filterMatrix();
}}

// ── Recent activity ────────────────────────────────────────────────────────
function clearActivityFilters() {{
  document.getElementById('activity-search').value = '';
  document.getElementById('activity-status').value = '';
  document.getElementById('activity-assignee').value = '';
  filterActivity();
}}
function filterActivity() {{
  const search = document.getElementById('activity-search').value.toLowerCase();
  const status = document.getElementById('activity-status').value.toUpperCase();
  const assignee = document.getElementById('activity-assignee').value;
  let count = 0;
  document.querySelectorAll('#activity-body tr').forEach(row => {{
    const show =
      (!search || row.textContent.toLowerCase().includes(search)) &&
      (!status || row.dataset.toStatus === status) &&
      (!assignee || row.dataset.assignee === assignee);
    row.style.display = show ? '' : 'none';
    if (show) count++;
  }});
  document.getElementById('activity-count').textContent = count + ' events';
}}

function renderActivity() {{
  const body = document.getElementById('activity-body');
  const actStatuses = new Set();
  const actAssignees = new Set();
  if (!EVENTS.length) {{ body.innerHTML='<tr><td colspan="7" class="empty">No events</td></tr>'; return; }}
  EVENTS.forEach(e => {{
    const fc = statusColor(e.from), tc = statusColor(e.to);
    const ts = (e.to||'').toUpperCase();
    const a = e.assignee||'';
    actStatuses.add(ts);
    if (a) actAssignees.add(a);
    body.innerHTML += `<tr data-to-status="${{ts}}" data-assignee="${{a}}">
      <td><a href="https://getground.atlassian.net/browse/${{e.key}}" target="_blank" style="color:var(--accent);text-decoration:none">${{e.key}}</a></td>
      <td>${{badge(e.from, fc)}}</td>
      <td>${{badge(e.to, tc)}}</td>
      <td style="color:var(--muted)">${{e.at}}</td>
      <td>${{e.by||'—'}}</td>
      <td>${{a||'—'}}</td>
      <td>${{e.due||'—'}}</td>
    </tr>`;
  }});
  populateSelect('activity-status', [...actStatuses].sort());
  populateSelect('activity-assignee', [...actAssignees].sort());
  filterActivity();
}}

// ── Overview charts ────────────────────────────────────────────────────────
(function() {{
  // Submitted sparkline
  const wrap = document.getElementById('submitted-chart');
  if (SUBMITTED.length) {{
    const max = Math.max(...SUBMITTED.map(d => +d.submitted_for_signature)) || 1;
    SUBMITTED.forEach(d => {{
      const pct = Math.round((+d.submitted_for_signature / max) * 100);
      const col = document.createElement('div');
      col.className = 'spark-col';
      col.innerHTML = `<div class="spark-val" style="color:var(--accent3)">${{d.submitted_for_signature}}</div><div class="spark-bar" style="height:${{Math.max(pct,2)}}%;min-height:4px;background:var(--accent3)"></div><div class="spark-label">${{(d.week||'').slice(-3)}}</div>`;
      wrap.appendChild(col);
    }});
  }}

  // WIP bars
  const wrapWip = document.getElementById('wip-chart');
  if (WIP.length) {{
    const max = Math.max(...WIP.map(d => +d.wip_count)) || 1;
    WIP.forEach((d,i) => {{
      const pct = Math.round((+d.wip_count/max)*100);
      const color = COLORS[i%COLORS.length];
      wrapWip.innerHTML += `<div class="bar-row"><div class="bar-label" title="${{d.status}}">${{d.status}}</div><div class="bar-track"><div class="bar-fill" style="width:${{Math.max(pct,2)}}%;background:${{color}}">${{d.wip_count}}</div></div></div>`;
    }});
  }}

  // Stuck by status
  const wrapStuck = document.getElementById('stuck-by-status');
  if (AGING.length) {{
    const byStatus = {{}};
    AGING.forEach(r => {{ byStatus[r.current_status] = (byStatus[r.current_status]||0)+1; }});
    const sorted = Object.entries(byStatus).sort((a,b)=>b[1]-a[1]).slice(0,6);
    const max = sorted[0]?.[1] || 1;
    sorted.forEach(([s,n],i) => {{
      const pct = Math.round((n/max)*100);
      const color = COLORS[i%COLORS.length];
      wrapStuck.innerHTML += `<div class="bar-row"><div class="bar-label" title="${{s}}">${{s}}</div><div class="bar-track"><div class="bar-fill" style="width:${{Math.max(pct,2)}}%;background:${{color}}">${{n}} accounts</div></div></div>`;
    }});
  }}

  // Reopen chart
  const wrapReopen = document.getElementById('reopen-chart');
  if (REOPEN.length) {{
    const max = Math.max(...REOPEN.map(d => +(d.reopen_rate_pct||0))) || 1;
    REOPEN.forEach(d => {{
      const pct = Math.round((+(d.reopen_rate_pct||0)/max)*100);
      const color = +d.reopen_rate_pct > 5 ? '#f97b4f' : +d.reopen_rate_pct > 3 ? '#f9c44f' : '#4fca8f';
      wrapReopen.innerHTML += `<div class="bar-row"><div class="bar-label">${{(d.week||'').slice(-3)}}</div><div class="bar-track"><div class="bar-fill" style="width:${{Math.max(pct,2)}}%;background:${{color}}">${{d.reopen_rate_pct}}%</div></div><div class="bar-val">${{d.reopens||0}}/${{d.tickets_done||0}}</div></div>`;
    }});
  }}
}})();

// ── Render all tables on load ──────────────────────────────────────────────
renderStuck();
renderMatrix();
renderActivity();
</script>
</body>
</html>"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    log.info("Dashboard written to %s", output_path)
    return output_path
