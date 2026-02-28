"""
Jira Movement Reporter — main entry point.
Usage:
    python jira_sheet_sync.py            # normal daily run
    python jira_sheet_sync.py --dry-run  # print what would be written, no Sheet writes
"""

import argparse
import logging
import sys
from datetime import datetime

from src.config import load_config
from src.jira_client import JiraClient
from src.changelog_parser import parse_movement_events
from src.metrics import compute_metrics
from src.sheets_writer import SheetsWriter
from src.dashboard import generate_dashboard

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("main")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Skip all Sheet writes")
    parser.add_argument("--config", default="config.yaml", help="Path to config file")
    args = parser.parse_args()

    log.info("=== Jira Movement Reporter starting (dry_run=%s) ===", args.dry_run)
    cfg = load_config(args.config)

    jira = JiraClient(cfg)
    writer = SheetsWriter(cfg, dry_run=args.dry_run)

    # 1. Pull raw issues (latest snapshot)
    log.info("Fetching issues …")
    issues = jira.fetch_issues()
    log.info("Fetched %d issues", len(issues))

    # 2. Pull changelogs for every issue
    log.info("Fetching changelogs …")
    changelogs = jira.fetch_changelogs(issues)

    # 3. Derive movement events (status transitions only)
    log.info("Parsing movement events …")
    events = parse_movement_events(issues, changelogs, cfg)
    log.info("Derived %d movement events", len(events))

    # 4. Compute KPIs
    log.info("Computing metrics …")
    metrics = compute_metrics(events, issues, cfg)

    # 5. Write to Google Sheets
    log.info("Writing to Google Sheets …")
    writer.write_all(issues, changelogs, events, metrics)

    # 6. Generate HTML dashboard
    log.info("Generating HTML dashboard …")
    path = generate_dashboard(issues, events, metrics)
    log.info("Dashboard ready → open %s in your browser", path)

    log.info("=== Done at %s ===", datetime.utcnow().isoformat())


if __name__ == "__main__":
    main()
