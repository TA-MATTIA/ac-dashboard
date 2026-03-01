"""
src/sheets_writer.py — write / update all Sheet tabs.

Strategy per tab:
  - config             : never overwritten by the script
  - raw_issues_snapshot: full replace each run
  - raw_changelog_snapshot: full replace each run
  - movement_events    : append-only, deduplicated by event_id
  - metrics            : full replace each run
  - dashboard          : never touched by the script (charts live here)
"""

import logging
from typing import Any, Dict, List, Optional

import gspread
from google.oauth2.service_account import Credentials

from .config import Config
from .changelog_parser import MOVEMENT_COLUMNS, events_to_rows
from .status_matrix import compute_status_durations

log = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]

# Column layout for raw_changelog_snapshot
CHANGELOG_COLUMNS = [
    "issue_key", "changelog_id", "changed_at", "changed_by",
    "field", "from_value", "to_value",
]


class SheetsWriter:
    def __init__(self, cfg: Config, dry_run: bool = False):
        self.cfg = cfg
        self.dry_run = dry_run
        self._gc: Optional[gspread.Client] = None
        self._sh = None

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def write_all(
        self,
        issues: List[Dict],
        changelogs: Dict[str, List[Dict]],
        events: List[Dict],
        metrics: Dict[str, List[List[Any]]],
    ):
        if self.dry_run:
            log.info("[DRY RUN] Would write %d issues, %d events to Sheet %s",
                     len(issues), len(events), self.cfg.google_sheet_id)
            self._print_dry_run_summary(issues, events, metrics)
            return

        sh = self._get_sheet()
        self._ensure_tabs(sh)

        self._replace_tab(sh, "raw_issues_snapshot", self._issues_to_rows(issues))
        self._replace_tab(sh, "raw_changelog_snapshot", self._changelogs_to_rows(changelogs))
        self._upsert_movement_events(sh, events)
        self._write_metrics(sh, metrics)
        self._write_status_matrix(sh, issues, events)
        log.info("All tabs written successfully.")

    # ------------------------------------------------------------------
    # Sheet helpers
    # ------------------------------------------------------------------

    def _get_sheet(self):
        if self._sh is None:
            creds = Credentials.from_service_account_file(
                self.cfg.google_service_account_file, scopes=SCOPES
            )
            gc = gspread.authorize(creds)
            self._sh = gc.open_by_key(self.cfg.google_sheet_id)
        return self._sh

    def _ensure_tabs(self, sh):
        """Create tabs that don't exist yet. Never delete existing ones."""
        existing = {ws.title for ws in sh.worksheets()}
        required = [
            "config",
            "raw_issues_snapshot",
            "raw_changelog_snapshot",
            "movement_events",
            "metrics",
            "status_durations_long",
            "status_matrix",
            "dashboard",
        ]
        for name in required:
            if name not in existing:
                sh.add_worksheet(title=name, rows=5000, cols=30)
                log.info("Created tab: %s", name)
                if name == "movement_events":
                    ws = sh.worksheet(name)
                    ws.append_row(MOVEMENT_COLUMNS)
                elif name == "config":
                    self._seed_config_tab(sh.worksheet(name))

    def _replace_tab(self, sh, tab_name: str, rows: List[List[Any]]):
        ws = sh.worksheet(tab_name)
        ws.clear()
        if rows:
            ws.update(rows, value_input_option="USER_ENTERED")
        log.info("Replaced tab '%s' with %d rows", tab_name, len(rows))

    def _upsert_movement_events(self, sh, new_events: List[Dict]):
        """Append only events not already in the Sheet (dedup by event_id)."""
        ws = sh.worksheet("movement_events")
        existing_data = ws.get_all_values()

        # Collect existing event_ids
        existing_ids = set()
        if len(existing_data) > 1:  # has header + data rows
            id_col_idx = MOVEMENT_COLUMNS.index("event_id")
            for row in existing_data[1:]:
                if len(row) > id_col_idx:
                    existing_ids.add(row[id_col_idx])

        to_append = [e for e in new_events if e["event_id"] not in existing_ids]
        if not to_append:
            log.info("movement_events: no new events to append")
            return

        rows = [[ev.get(c, "") for c in MOVEMENT_COLUMNS] for ev in to_append]
        ws.append_rows(rows, value_input_option="USER_ENTERED")
        log.info("Appended %d new movement events (skipped %d duplicates)",
                 len(to_append), len(new_events) - len(to_append))

    def _write_metrics(self, sh, metrics: Dict[str, List[List[Any]]]):
        """Write all metric tables to the metrics tab, stacked vertically."""
        ws = sh.worksheet("metrics")
        ws.clear()

        all_rows = []
        for name, table in metrics.items():
            all_rows.append([f"=== {name.upper()} ==="])
            all_rows.extend(table)
            all_rows.append([])  # blank separator

        if all_rows:
            ws.update(all_rows, value_input_option="USER_ENTERED")
        log.info("Metrics tab written: %d tables", len(metrics))

    # ------------------------------------------------------------------
    # Data serialisers
    # ------------------------------------------------------------------

    def _issues_to_rows(self, issues: List[Dict]) -> List[List[Any]]:
        cols = [
            "key", "project", "issue_type", "priority", "summary",
            "status", "assignee", "reporter", "created", "resolved",
            "labels", "components", "team_field",
        ]
        rows = [cols]
        for i in issues:
            rows.append([i.get(c, "") for c in cols])
        return rows

    def _changelogs_to_rows(self, changelogs: Dict[str, List[Dict]]) -> List[List[Any]]:
        rows = [CHANGELOG_COLUMNS]
        for issue_key, entries in changelogs.items():
            for entry in entries:
                author = (entry.get("author") or {}).get("displayName", "")
                changed_at = entry.get("created", "")
                cl_id = entry.get("id", "")
                for item in entry.get("items", []):
                    rows.append([
                        issue_key,
                        cl_id,
                        changed_at,
                        author,
                        item.get("field", ""),
                        item.get("fromString") or "",
                        item.get("toString") or "",
                    ])
        return rows

    # ------------------------------------------------------------------
    # Config tab seed
    # ------------------------------------------------------------------

    def _seed_config_tab(self, ws):
        """Pre-populate config tab with labelled cells for easy manual editing."""
        cfg = self.cfg
        rows = [
            ["CONFIGURATION", "", ""],
            ["", "", ""],
            ["Setting", "Value", "Notes"],
            ["jira_base_url", cfg.jira_base_url, "Your Jira Cloud URL"],
            ["project_keys", ",".join(cfg.jira_project_keys), "Comma-separated project keys"],
            ["backfill_from", cfg.backfill_from, "Start date for data pull"],
            ["in_progress_statuses", ",".join(cfg.in_progress_statuses), "Statuses treated as In Progress"],
            ["done_statuses", ",".join(cfg.done_statuses), "Statuses treated as Done"],
            ["team_field", cfg.team_field, "component | label | custom field id"],
            ["last_run", "", "Auto-filled by script"],
        ]
        ws.update(rows)

    # ------------------------------------------------------------------
    # Dry-run
    # ------------------------------------------------------------------

    def _write_status_matrix(self, sh, issues, events):
        """Compute and write status_durations_long and status_matrix tabs."""
        long_rows, matrix_rows = compute_status_durations(events, issues)

        # status_durations_long — full replace
        ws_long = sh.worksheet("status_durations_long")
        ws_long.clear()
        if long_rows:
            # Write in chunks to avoid API limits
            chunk = 1000
            for i in range(0, len(long_rows), chunk):
                ws_long.append_rows(long_rows[i:i+chunk], value_input_option="USER_ENTERED")
        log.info("status_durations_long: %d rows", len(long_rows) - 1)

        # status_matrix — full replace
        ws_matrix = sh.worksheet("status_matrix")
        ws_matrix.clear()
        if matrix_rows:
            chunk = 500
            for i in range(0, len(matrix_rows), chunk):
                ws_matrix.append_rows(matrix_rows[i:i+chunk], value_input_option="USER_ENTERED")

            # Format header row bold
            try:
                ws_matrix.format("1:1", {"textFormat": {"bold": True}})
            except Exception:
                pass

            # Freeze header row and issue_key column
            try:
                ws_matrix.freeze(rows=1, cols=1)
            except Exception:
                pass

        log.info("status_matrix: %d issues", len(matrix_rows) - 1)

    def _print_dry_run_summary(self, issues, events, metrics):
        print(f"\n{'='*60}")
        print("DRY RUN SUMMARY")
        print(f"{'='*60}")
        print(f"Issues:          {len(issues)}")
        print(f"Movement events: {len(events)}")
        for name, table in metrics.items():
            print(f"Metric '{name}': {len(table)-1} rows")
        if events:
            print("\nSample events (first 3):")
            for ev in events[:3]:
                print(f"  {ev['issue_key']:12s}  {ev['from_status']:20s} → {ev['to_status']:20s}  {ev['changed_at']}")
        print(f"{'='*60}\n")
