"""
src/dashboard.py — generate a self-contained HTML dashboard from metrics + events.
"""

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

log = logging.getLogger(__name__)


def _get_week_label(offset=0):
    """Get ISO week label for current week (offset=0) or last week (offset=-1)."""
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

    throughput    = _extract("throughput")
    submitted     = _extract("submitted_for_signature")
    cycle_time    = _extract("cycle_time")
    wip           = _extract("wip")
    aging         = _extract("aging_wip")
    reopen        = _extract("reopen_rate")
    tis           = _extract("time_in_status")

    # ── KPI: Submitted for Signature this week vs last week ──────────────────
    this_week = _get_week_label(0)
    last_week = _get_week_label(-1)

    def _week_val(table, week, col):
        row = next((r for r in table if r.get("week") == week), {})
        return int(row.get(col, 0))

    sig_this  = _week_val(submitted, this_week, "submitted_for_signature")
    sig_last  = _week_val(submitted, last_week, "submitted_for_signature")
    sig_delta = sig_this - sig_last

    # ── KPI: WIP total (exclude Done) ────────────────────────────────────────
    total_wip = sum(int(r.get("wip_count", 0)) for r in wip)

    # ── KPI: Cycle time overall ───────────────────────────────────────────────
    overall_ct   = next((r for r in cycle_time if r.get("group") == "Overall"), {})
    cycle_avg    = overall_ct.get("cycle_avg_h", "")
    cycle_avg_d  = round(float(cycle_avg) / 24, 1) if cycle_avg else "—"
    cycle_p50_d  = round(float(overall_ct.get("cycle_p50_h", 0)) / 24, 1) if overall_ct.get("cycle_p50_h") else "—"
    cycle_p90_d  = round(float(overall_ct.get("cycle_p90_h", 0)) / 24, 1) if overall_ct.get("cycle_p90_h") else "—"

    # ── KPI: Reopen rate ─────────────────────────────────────────────────────
    reopen_rate  = reopen[-1].get("reopen_rate_pct", "—") if reopen else "—"

    # ── Aging counts (already excludes Done + Due) ────────────────────────────
    stuck_5  = len(aging)
    stuck_10 = sum(1 for r in aging if r.get("bucket") in (">10d", ">30d"))
    stuck_30 = sum(1 for r in aging if r.get("bucket") == ">30d")

    last_sync    = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    total_issues = len(issues)

    recent_events = sorted(events, key=lambda e: e.get("changed_at", ""), reverse=True)[:50]

    # JSON payloads
    submitted_json  = json.dumps(submitted)
    throughput_json = json.dumps(throughput)
    wip_json        = json.dumps(wip[:12])
    aging_json      = json.dumps(sorted(aging, key=lambda r: int(r.get("days_in_status", 0)), reverse=True))
    reopen_json     = json.dumps(reopen)
    tis_json        = json.dumps(sorted(tis, key=lambda r: float(r.get("avg_hours", 0)), reverse=True)[:10])
    assignee_ct     = [r for r in cycle_time if r.get("group", "").startswith("Assignee:")]
    assignee_json   = json.dumps(assignee_ct[:10])
    events_json     = json.dumps([{
        "key":      e.get("issue_key", ""),
        "from":     e.get("from_status", ""),
        "to":       e.get("to_status", ""),
        "at":       e.get("changed_at", "")[:16].replace("T", " "),
        "by":       e.get("changed_by", ""),
        "assignee": e.get("assignee", ""),
        "due":      e.get("team_field", ""),
    } for e in recent_events])

    sig_trend = ('↑ ' + str(abs(sig_delta)) + ' vs last week') if sig_delta >= 0 else ('↓ ' + str(abs(sig_delta)) + ' vs last week')
    sig_trend_class = 'trend-up' if sig_delta >= 0 else 'trend-down'

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AC Project — Jira Dashboard</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=Instrument+Serif:ital@0;1&family=DM+Sans:wght@300;400;500;600&display=swap');
  :root {{
    --bg:#0f1117;--surface:#181c27;--surface2:#1e2334;--border:#2a3045;
    --accent:#4f9cf9;--accent2:#f97b4f;--accent3:#4fca8f;--accent4:#c97bf9;
    --text:#e8ecf4;--muted:#6b7699;--warn:#f9c44f;--danger:#f97b4f;
  }}
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{background:var(--bg);color:var(--text);font-family:'DM Sans',sans-serif;font-size:13px;min-height:100vh}}
  .header{{padding:24px 32px 18px;border-bottom:1px solid var(--border);display:flex;align-items:baseline;gap:16px;flex-wrap:wrap}}
  .header h1{{font-family:'Instrument Serif',serif;font-size:24px;font-weight:400}}
  .header .meta{{font-family:'DM Mono',monospace;font-size:11px;color:var(--muted);letter-spacing:.05em}}
  .tab-bar{{display:flex;gap:2px;padding:12px 32px 0;border-bottom:1px solid var(--border);overflow-x:auto}}
  .tab{{padding:8px 16px;font-size:12px;color:var(--muted);cursor:pointer;border-bottom:2px solid transparent;white-space:nowrap;transition:all .15s;font-family:'DM Mono',monospace}}
  .tab:hover{{color:var(--text)}} .tab.active{{color:var(--accent);border-bottom-color:var(--accent)}}
  .content{{padding:24px 32px;display:none}} .content.active{{display:block;animation:fadeIn .2s ease}}
  @keyframes fadeIn{{from{{opacity:0;transform:translateY(4px)}}to{{opacity:1;transform:translateY(0)}}}}
  .kpi-row{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:20px}}
  .kpi{{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:16px 18px}}
  .kpi .label{{font-size:11px;color:var(--muted);font-family:'DM Mono',monospace;letter-spacing:.04em;margin-bottom:8px;text-transform:uppercase}}
  .kpi .value{{font-size:30px;font-family:'Instrument Serif',serif;font-weight:400;line-height:1}}
  .kpi .sub{{font-size:11px;color:var(--muted);margin-top:4px}}
  .trend-up{{color:var(--accent3)}} .trend-down{{color:var(--danger)}}
  .grid2{{display:grid;grid-template-columns:1.6fr 1fr;gap:12px;margin-bottom:12px}}
  .grid3{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:12px}}
  .card{{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:18px 20px}}
  .card h3{{font-size:11px;color:var(--muted);font-family:'DM Mono',monospace;letter-spacing:.06em;text-transform:uppercase;margin-bottom:16px}}
  .bar-chart{{display:flex;flex-direction:column;gap:6px}}
  .bar-row{{display:flex;align-items:center;gap:8px}}
  .bar-label{{width:160px;font-size:10px;color:var(--muted);text-align:right;flex-shrink:0;font-family:'DM Mono',monospace;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
  .bar-track{{flex:1;background:var(--surface2);border-radius:2px;height:18px;overflow:hidden}}
  .bar-fill{{height:100%;border-radius:2px;display:flex;align-items:center;padding-left:6px;font-size:10px;font-family:'DM Mono',monospace;color:rgba(255,255,255,.8);white-space:nowrap}}
  .bar-val{{width:40px;font-size:10px;font-family:'DM Mono',monospace;color:var(--muted);text-align:right;flex-shrink:0}}
  .spark-wrap{{display:flex;align-items:flex-end;gap:5px;height:90px}}
  .spark-col{{display:flex;flex-direction:column;align-items:center;gap:3px;flex:1;cursor:default}}
  .spark-bar{{width:100%;background:var(--accent);border-radius:3px 3px 0 0;opacity:.8;transition:opacity .2s}}
  .spark-bar:hover{{opacity:1}}
  .spark-label{{font-size:9px;color:var(--muted);font-family:'DM Mono',monospace}}
  .spark-val{{font-size:9px;color:var(--accent);font-family:'DM Mono',monospace}}
  table{{width:100%;border-collapse:collapse}}
  th{{font-size:10px;color:var(--muted);font-family:'DM Mono',monospace;text-transform:uppercase;letter-spacing:.06em;padding:6px 8px;text-align:left;border-bottom:1px solid var(--border)}}
  td{{padding:7px 8px;font-size:11px;border-bottom:1px solid #1a1f2e;font-family:'DM Mono',monospace}}
  tr:last-child td{{border-bottom:none}} tr:hover td{{background:var(--surface2)}}
  .badge{{display:inline-block;padding:2px 7px;border-radius:10px;font-size:10px;font-family:'DM Mono',monospace}}
  .aging-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:16px}}
  .aging-box{{background:var(--surface2);border-radius:6px;padding:14px;text-align:center}}
  .aging-num{{font-size:36px;font-family:'Instrument Serif',serif;line-height:1;margin-bottom:4px}}
  .aging-lbl{{font-size:10px;color:var(--muted);font-family:'DM Mono',monospace}}
  .note{{background:rgba(79,156,249,.08);border:1px solid rgba(79,156,249,.2);border-radius:6px;padding:10px 14px;font-size:12px;color:var(--accent);margin-bottom:16px}}
  .overflow{{overflow-x:auto}}
  @media(max-width:900px){{.kpi-row{{grid-template-columns:repeat(2,1fr)}}.grid2{{grid-template-columns:1fr}}.grid3{{grid-template-columns:1fr}}}}
</style>
</head>
<body>
<div class="header">
  <h1>AC Project — Jira Dashboard</h1>
  <span class="meta">last sync: {last_sync} &nbsp;·&nbsp; {total_issues:,} issues</span>
</div>
<div class="tab-bar">
  <div class="tab active" onclick="show('dashboard',this)">dashboard</div>
  <div class="tab" onclick="show('aging',this)">stuck tickets</div>
  <div class="tab" onclick="show('cycle',this)">cycle time</div>
  <div class="tab" onclick="show('activity',this)">recent activity</div>
</div>

<!-- DASHBOARD -->
<div class="content active" id="dashboard">
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
      <div class="value" style="color:var(--text)">{total_wip}</div>
      <div class="sub">open tickets right now</div>
    </div>
    <div class="kpi">
      <div class="label">Reopen Rate</div>
      <div class="value" style="color:var(--warn)">{reopen_rate}<span style="font-size:14px">%</span></div>
      <div class="sub">tickets moved back from Done</div>
    </div>
  </div>
  <div class="grid2">
    <div class="card"><h3>Weekly — Submitted for Signature</h3><div class="spark-wrap" id="submitted-chart"></div></div>
    <div class="card"><h3>WIP by Status</h3><div class="bar-chart" id="wip-chart"></div></div>
  </div>
  <div class="grid2">
    <div class="card">
      <h3>Stuck Tickets Summary (excl. Done &amp; Due)</h3>
      <div class="aging-grid">
        <div class="aging-box"><div class="aging-num" style="color:var(--warn)">{stuck_5}</div><div class="aging-lbl">stuck &gt;5 days</div></div>
        <div class="aging-box"><div class="aging-num" style="color:var(--danger)">{stuck_10}</div><div class="aging-lbl">stuck &gt;10 days</div></div>
        <div class="aging-box"><div class="aging-num" style="color:#ff4444">{stuck_30}</div><div class="aging-lbl">stuck &gt;30 days</div></div>
      </div>
      <div class="bar-chart" id="stuck-chart"></div>
    </div>
    <div class="card"><h3>Reopen Rate per Week</h3><div class="bar-chart" id="reopen-chart"></div></div>
  </div>
</div>

<!-- STUCK TICKETS -->
<div class="content" id="aging">
  <div class="note">⚠️ Tickets stuck in the same status for 5+ days. Done and Due statuses are excluded. Sorted by most stuck first.</div>
  <div style="margin-bottom:12px;font-family:'DM Mono',monospace;font-size:11px;color:var(--muted)">
    Showing <span id="aging-count" style="color:var(--text)">0</span> stuck tickets
  </div>
  <div class="overflow">
    <table>
      <thead><tr><th>ticket</th><th>status</th><th>assignee</th><th>days stuck</th><th>accounting due</th><th>bucket</th></tr></thead>
      <tbody id="aging-body"></tbody>
    </table>
  </div>
</div>

<!-- CYCLE TIME -->
<div class="content" id="cycle">
  <div class="grid2">
    <div class="card"><h3>Cycle Time by Assignee (days: avg / p90)</h3><div class="bar-chart" id="cycle-chart"></div></div>
    <div class="card"><h3>Time Spent in Each Status (avg days)</h3><div class="bar-chart" id="tis-chart"></div></div>
  </div>
</div>

<!-- RECENT ACTIVITY -->
<div class="content" id="activity">
  <div class="note">📋 Last 50 status transitions, most recent first.</div>
  <div class="overflow">
    <table>
      <thead><tr><th>ticket</th><th>from</th><th>to</th><th>when</th><th>by</th><th>assignee</th><th>accounting due</th></tr></thead>
      <tbody id="events-body"></tbody>
    </table>
  </div>
</div>

<script>
const SUBMITTED  = {submitted_json};
const THROUGHPUT = {throughput_json};
const WIP        = {wip_json};
const AGING      = {aging_json};
const REOPEN     = {reopen_json};
const TIS        = {tis_json};
const ASSIGNEE   = {assignee_json};
const EVENTS     = {events_json};

const COLORS = ['#4f9cf9','#c97bf9','#f9c44f','#4fca8f','#f97b4f','#4fc9f9','#f94f9c','#9cf94f','#f9a44f','#4f4ff9'];

function show(id, el) {{
  document.querySelectorAll('.content').forEach(e => e.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(e => e.classList.remove('active'));
  document.getElementById(id).classList.add('active');
  el.classList.add('active');
}}

function badge(text, color) {{
  return `<span class="badge" style="background:${{color}}22;color:${{color}}">${{text}}</span>`;
}}

function statusColor(s) {{
  s = (s||'').toLowerCase();
  if (s.includes('filed') || s.includes('ct600')) return '#4fca8f';
  if (s.includes('signed')) return '#4fca8f';
  if (s.includes('further info') || s.includes('pending')) return '#f9c44f';
  if (s.includes('review')) return '#c97bf9';
  if (s.includes('submitted')) return '#4f9cf9';
  if (s.includes('preparing')) return '#4fc9f9';
  if (s.includes('customer')) return '#6b7699';
  return '#6b7699';
}}

// Submitted for Signature chart
(function() {{
  const wrap = document.getElementById('submitted-chart');
  if (!SUBMITTED.length) {{ wrap.innerHTML='<div style="color:var(--muted);font-size:12px;padding:20px">No data yet</div>'; return; }}
  const max = Math.max(...SUBMITTED.map(d => +d.submitted_for_signature));
  SUBMITTED.slice(-12).forEach(d => {{
    const pct = Math.round((+d.submitted_for_signature / max) * 100);
    const col = document.createElement('div');
    col.className = 'spark-col';
    col.innerHTML = `<div class="spark-val">${{d.submitted_for_signature}}</div><div class="spark-bar" style="height:${{Math.max(pct,2)}}%;min-height:4px;background:var(--accent3)"></div><div class="spark-label">${{(d.week||'').slice(-3)}}</div>`;
    wrap.appendChild(col);
  }});
}})();

// WIP chart
(function() {{
  const wrap = document.getElementById('wip-chart');
  if (!WIP.length) return;
  const max = Math.max(...WIP.map(d => +d.wip_count));
  WIP.forEach((d,i) => {{
    const pct = Math.round((+d.wip_count / max) * 100);
    const color = COLORS[i % COLORS.length];
    wrap.innerHTML += `<div class="bar-row"><div class="bar-label" title="${{d.status}}">${{d.status}}</div><div class="bar-track"><div class="bar-fill" style="width:${{Math.max(pct,2)}}%;background:${{color}}">${{d.wip_count}}</div></div></div>`;
  }});
}})();

// Stuck where chart
(function() {{
  const wrap = document.getElementById('stuck-chart');
  if (!AGING.length) return;
  const byStatus = {{}};
  AGING.forEach(r => {{ byStatus[r.current_status] = (byStatus[r.current_status]||0)+1; }});
  const sorted = Object.entries(byStatus).sort((a,b)=>b[1]-a[1]).slice(0,6);
  const max = sorted[0]?.[1] || 1;
  sorted.forEach(([s,n],i) => {{
    const pct = Math.round((n/max)*100);
    const color = COLORS[i%COLORS.length];
    wrap.innerHTML += `<div class="bar-row"><div class="bar-label" title="${{s}}">${{s}}</div><div class="bar-track"><div class="bar-fill" style="width:${{Math.max(pct,2)}}%;background:${{color}}">${{n}} tickets</div></div></div>`;
  }});
}})();

// Reopen chart
(function() {{
  const wrap = document.getElementById('reopen-chart');
  if (!REOPEN.length) return;
  const max = Math.max(...REOPEN.map(d => +(d.reopen_rate_pct||0))) || 1;
  REOPEN.slice(-8).forEach(d => {{
    const pct = Math.round((+(d.reopen_rate_pct||0)/max)*100);
    const color = +d.reopen_rate_pct > 5 ? '#f97b4f' : +d.reopen_rate_pct > 3 ? '#f9c44f' : '#4fca8f';
    wrap.innerHTML += `<div class="bar-row"><div class="bar-label">${{(d.week||'').slice(-3)}}</div><div class="bar-track"><div class="bar-fill" style="width:${{Math.max(pct,2)}}%;background:${{color}}">${{d.reopen_rate_pct}}%</div></div><div class="bar-val">${{d.reopens||0}}/${{d.tickets_done||0}}</div></div>`;
  }});
}})();

// Stuck tickets full table
(function() {{
  const body = document.getElementById('aging-body');
  const counter = document.getElementById('aging-count');
  if (!AGING.length) {{
    body.innerHTML = '<tr><td colspan="6" style="color:var(--muted);text-align:center;padding:20px">No stuck tickets 🎉</td></tr>';
    return;
  }}
  counter.textContent = AGING.length;
  AGING.forEach(r => {{
    const days = +r.days_in_status;
    const dcolor = days>=30?'#ff4444':days>=10?'var(--danger)':'var(--warn)';
    const sc = statusColor(r.current_status);
    body.innerHTML += `<tr>
      <td style="color:var(--accent)">${{r.issue_key}}</td>
      <td>${{badge(r.current_status, sc)}}</td>
      <td>${{r.assignee||'—'}}</td>
      <td style="color:${{dcolor}};font-weight:500">${{days}}d</td>
      <td>${{r.team_field||'—'}}</td>
      <td>${{badge(r.bucket, dcolor)}}</td>
    </tr>`;
  }});
}})();

// Cycle time by assignee
(function() {{
  const wrap = document.getElementById('cycle-chart');
  if (!ASSIGNEE.length) {{ wrap.innerHTML='<div style="color:var(--muted);font-size:12px">Not enough data yet</div>'; return; }}
  const max = Math.max(...ASSIGNEE.map(d=>+(d.cycle_avg_h||0)/24)) || 1;
  ASSIGNEE.forEach((d,i) => {{
    const name = (d.group||'').replace('Assignee: ','');
    const days = +(d.cycle_avg_h||0)/24;
    const p90  = +(d.cycle_p90_h||0)/24;
    const pct  = Math.round((days/max)*100);
    wrap.innerHTML += `<div class="bar-row"><div class="bar-label" title="${{name}}">${{name}}</div><div class="bar-track"><div class="bar-fill" style="width:${{Math.max(pct,2)}}%;background:var(--accent)">${{days.toFixed(1)}}d avg</div></div><div class="bar-val">p90:${{p90.toFixed(0)}}d</div></div>`;
  }});
}})();

// Time in status
(function() {{
  const wrap = document.getElementById('tis-chart');
  if (!TIS.length) return;
  const max = Math.max(...TIS.map(d=>+(d.avg_hours||0)/24)) || 1;
  TIS.forEach((d,i) => {{
    const days = (+(d.avg_hours||0)/24).toFixed(1);
    const pct  = Math.round((+(d.avg_hours||0)/24/max)*100);
    const color = COLORS[i%COLORS.length];
    wrap.innerHTML += `<div class="bar-row"><div class="bar-label" title="${{d.status}}">${{d.status}}</div><div class="bar-track"><div class="bar-fill" style="width:${{Math.max(pct,2)}}%;background:${{color}}">${{days}}d</div></div></div>`;
  }});
}})();

// Recent events
(function() {{
  const body = document.getElementById('events-body');
  if (!EVENTS.length) {{ body.innerHTML='<tr><td colspan="7" style="color:var(--muted);text-align:center;padding:20px">No events yet</td></tr>'; return; }}
  EVENTS.forEach(e => {{
    const fc=statusColor(e.from), tc=statusColor(e.to);
    body.innerHTML+=`<tr>
      <td style="color:var(--accent)">${{e.key}}</td>
      <td>${{badge(e.from,fc)}}</td>
      <td>${{badge(e.to,tc)}}</td>
      <td style="color:var(--muted)">${{e.at}}</td>
      <td>${{e.by||'—'}}</td>
      <td>${{e.assignee||'—'}}</td>
      <td>${{e.due||'—'}}</td>
    </tr>`;
  }});
}})();
</script>
</body>
</html>"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    log.info("Dashboard written to %s", output_path)
    return output_path


def _extract_table(table):
    if not table or len(table) < 2:
        return []
    header = table[0]
    return [dict(zip(header, row)) for row in table[1:]]
