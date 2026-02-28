"""
jira_sheet_sync.py — main entry point.

Usage:
  python3 jira_sheet_sync.py           # normal run (uses cache)
  python3 jira_sheet_sync.py --dry-run # no writes to Sheets or disk
  python3 jira_sheet_sync.py --full    # ignore cache, re-fetch everything
"""

import argparse
import logging
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s.%(msecs)03d [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

from src.config import load_config
from src.jira_client import JiraClient
from src.changelog_parser import parse_movement_events
from src.metrics import compute_metrics
from src.sheets_writer import SheetsWriter
from src.dashboard import generate_dashboard
from src.cache import load_cache, save_cache, merge_updated_issues


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--full", action="store_true", help="Ignore cache, re-fetch everything")
    args = parser.parse_args()

    log.info("=== Jira Movement Reporter starting (dry_run=%s, full=%s) ===", args.dry_run, args.full)

    cfg = load_config()
    jira = JiraClient(cfg)

    # 1. Load cache
    cached_issues, cached_changelogs, last_sync = None, None, None
    if not args.full and not args.dry_run:
        cached_issues, cached_changelogs, last_sync = load_cache(cfg)

    # 2. Fetch issues
    if cached_issues is not None and last_sync:
        log.info("Incremental fetch — issues updated since %s ...", last_sync[:19])
        jira.set_incremental_since(last_sync)
        updated_issues = jira.fetch_issues()
        log.info("Fetched %d updated issues (out of %d cached)", len(updated_issues), len(cached_issues))
    else:
        log.info("Full fetch — fetching all issues ...")
        updated_issues = jira.fetch_issues()
        log.info("Fetched %d issues", len(updated_issues))

    # 3. Fetch changelogs
    if cached_issues is not None and last_sync:
        log.info("Fetching changelogs for %d updated issues ...", len(updated_issues))
        updated_changelogs = jira.fetch_changelogs(updated_issues)
        issues, changelogs = merge_updated_issues(
            cached_issues, updated_issues, updated_changelogs, cached_changelogs
        )
    else:
        log.info("Fetching changelogs for all %d issues ...", len(updated_issues))
        changelogs = jira.fetch_changelogs(updated_issues)
        issues = updated_issues

    log.info("Total: %d issues, %d changelog entries", len(issues), sum(len(v) for v in changelogs.values()))

    # 4. Parse movement events
    log.info("Parsing movement events ...")
    events = parse_movement_events(issues, changelogs, cfg)
    log.info("Derived %d movement events", len(events))

    # 5. Compute metrics
    log.info("Computing metrics ...")
    metrics = compute_metrics(events, issues, cfg)

    # 6. Save cache
    if not args.dry_run:
        save_cache(cfg, issues, changelogs)

    # 7. Write to Google Sheets
    writer = SheetsWriter(cfg)
    if args.dry_run:
        log.info("[DRY RUN] Would write %d issues, %d events to Sheet %s",
                 len(issues), len(events), cfg.google_sheet_id)
        _dry_run_summary(issues, events, metrics)
    else:
        log.info("Writing to Google Sheets ...")
        writer.write_all(issues, changelogs, events, metrics)

    # 8. Generate HTML dashboard
    log.info("Generating HTML dashboard ...")
    path = generate_dashboard(issues, events, metrics)
    log.info("Dashboard ready -> open %s in your browser", path)

    log.info("=== Done at %s ===", datetime.now(timezone.utc).isoformat())


def _dry_run_summary(issues, events, metrics):
    print("\n" + "="*60)
    print("DRY RUN SUMMARY")
    print("="*60)
    print(f"Issues:          {len(issues)}")
    print(f"Movement events: {len(events)}")
    for name, table in metrics.items():
        print(f"Metric '{name}': {len(table)-1} rows")
    if events:
        print("\nSample events (first 3):")
        for ev in events[:3]:
            print(f"  {ev['issue_key']:12} {ev['from_status']:25} -> {ev['to_status']:25} {ev['changed_at']}")
    print("="*60)


if __name__ == "__main__":
    main()
