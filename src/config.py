"""
src/config.py — load config.yaml + environment variable overrides.
"""

import os
import yaml
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Config:
    # Jira
    jira_base_url: str
    jira_email: str
    jira_api_token: str
    jira_project_keys: List[str]
    jira_jql_override: Optional[str]       # if set, used instead of project keys
    jira_type: str                         # "software" | "service_management"
    in_progress_statuses: List[str]
    done_statuses: List[str]
    backfill_from: str

    # Team grouping
    team_field: str                        # "component" | "label" | custom field id
    team_field_name: str                   # display name for the column

    # Google Sheets
    google_sheet_id: str
    google_service_account_file: str

    # Behaviour
    page_size: int = 100
    max_retries: int = 5
    retry_backoff: float = 1.5


def load_config(path: str = "config.yaml") -> Config:
    with open(path, "r") as f:
        raw = yaml.safe_load(f)

    # Environment variable overrides (secrets should NOT be in the yaml)
    jira_base_url = os.environ.get("JIRA_BASE_URL") or raw["jira"]["base_url"]
    jira_email = os.environ.get("JIRA_EMAIL") or raw["jira"]["email"]
    jira_api_token = os.environ.get("JIRA_API_TOKEN") or raw["jira"]["api_token"]
    google_sheet_id = os.environ.get("GOOGLE_SHEET_ID") or raw["google"]["sheet_id"]
    google_sa_file = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE") or raw["google"]["service_account_file"]

    return Config(
        jira_base_url=jira_base_url.rstrip("/"),
        jira_email=jira_email,
        jira_api_token=jira_api_token,
        jira_project_keys=raw["jira"].get("project_keys", []),
        jira_jql_override=raw["jira"].get("jql_override"),
        jira_type=raw["jira"].get("type", "software"),
        in_progress_statuses=[s.lower() for s in raw["statuses"]["in_progress"]],
        done_statuses=[s.lower() for s in raw["statuses"]["done"]],
        backfill_from=raw.get("backfill_from", "2026-01-01"),
        team_field=raw["team"].get("field", "component"),
        team_field_name=raw["team"].get("field_name", "Team"),
        google_sheet_id=google_sheet_id,
        google_service_account_file=google_sa_file,
        page_size=raw.get("page_size", 100),
        max_retries=raw.get("max_retries", 5),
        retry_backoff=raw.get("retry_backoff", 1.5),
    )
