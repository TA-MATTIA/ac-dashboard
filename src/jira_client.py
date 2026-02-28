"""
src/jira_client.py — Jira Cloud REST API v3 client.
Features:
  - Paginated issue search (JQL)
  - Changelog retrieval via expand=changelog + /changelog endpoint for >100 pages
  - Exponential back-off on 429 / 5xx
"""

import hashlib
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Generator, List, Optional

import requests
from requests.auth import HTTPBasicAuth

from .config import Config

log = logging.getLogger(__name__)

FIELDS = [
    "summary", "status", "assignee", "reporter", "priority",
    "issuetype", "project", "created", "resolutiondate",
    "labels", "components", "customfield_10016",  # story points (adjust if needed)
]


class JiraClient:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.auth = HTTPBasicAuth(cfg.jira_email, cfg.jira_api_token)
        self.base = cfg.jira_base_url
        self.session = requests.Session()
        self.session.auth = self.auth
        self.session.headers.update({
            "Accept": "application/json",
            "Content-Type": "application/json",
        })

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch_issues(self) -> List[Dict]:
        """Return all issues matching the configured JQL as flat dicts."""
        jql = self._build_jql()
        log.info("JQL: %s", jql)
        issues = []
        for page in self._paginate_search(jql):
            issues.extend(page)
        return issues

    def fetch_changelogs(self, issues: List[Dict]) -> Dict[str, List[Dict]]:
        """Return {issue_key: [changelog_item, ...]} for all issues."""
        changelogs: Dict[str, List[Dict]] = {}
        total = len(issues)
        for idx, issue in enumerate(issues, 1):
            key = issue["key"]
            if idx % 50 == 0 or idx == total:
                log.info("  Changelogs: %d / %d", idx, total)
            changelogs[key] = list(self._fetch_issue_changelog(key))
        return changelogs

    # ------------------------------------------------------------------
    # JQL builder
    # ------------------------------------------------------------------

    def _build_jql(self) -> str:
        cfg = self.cfg
        if cfg.jira_jql_override:
            return cfg.jira_jql_override
        keys_clause = " OR ".join(f"project = {k}" for k in cfg.jira_project_keys)
        return f"({keys_clause}) AND updated >= \"{cfg.backfill_from}\" ORDER BY updated ASC"
        return f"({keys_clause}) AND updated >= '{since}' ORDER BY updated ASC"

    # ------------------------------------------------------------------
    # Pagination helpers
    # ------------------------------------------------------------------

    def _paginate_search(self, jql: str):
        page_size = self.cfg.page_size
        next_page_token = None
        while True:
            params = {"jql": jql, "maxResults": page_size, "fields": ",".join(FIELDS)}
            if next_page_token:
                params["nextPageToken"] = next_page_token
            data = self._get(f"{self.base}/rest/api/3/search/jql", params=params)
            issues = data.get("issues", [])
            if not issues:
                break
            yield [self._flatten_issue(i) for i in issues]
            if data.get("isLast", True):
                break
            next_page_token = data.get("nextPageToken")
            if not next_page_token:
                break

    def _fetch_issue_changelog(self, issue_key: str) -> Generator[Dict, None, None]:
        """Fetch changelog entries for a single issue using the dedicated endpoint."""
        start = 0
        page_size = 100
        while True:
            data = self._get(
                f"{self.base}/rest/api/3/issue/{issue_key}/changelog",
                params={"startAt": start, "maxResults": page_size},
            )
            values = data.get("values", [])
            if not values:
                break
            for entry in values:
                yield entry
            start += len(values)
            if start >= data.get("total", 0):
                break

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    def _get(self, url: str, params: Optional[Dict] = None) -> Dict:
        backoff = 1.0
        for attempt in range(self.cfg.max_retries):
            try:
                resp = self.session.get(url, params=params, timeout=30)
                if resp.status_code == 429:
                    retry_after = float(resp.headers.get("Retry-After", backoff))
                    log.warning("Rate-limited. Sleeping %.1fs …", retry_after)
                    time.sleep(retry_after)
                    backoff *= self.cfg.retry_backoff
                    continue
                if resp.status_code >= 500:
                    log.warning("Server error %d on attempt %d", resp.status_code, attempt + 1)
                    time.sleep(backoff)
                    backoff *= self.cfg.retry_backoff
                    continue
                resp.raise_for_status()
                return resp.json()
            except requests.RequestException as exc:
                if attempt == self.cfg.max_retries - 1:
                    raise
                log.warning("Request error: %s — retrying …", exc)
                time.sleep(backoff)
                backoff *= self.cfg.retry_backoff
        raise RuntimeError(f"Exceeded max retries for {url}")

    # ------------------------------------------------------------------
    # Flatteners
    # ------------------------------------------------------------------

    def _flatten_issue(self, raw: Dict) -> Dict:
        f = raw.get("fields", {})
        cfg = self.cfg

        assignee = (f.get("assignee") or {}).get("displayName", "")
        components = ",".join(c["name"] for c in (f.get("components") or []))
        labels = ",".join(f.get("labels") or [])

        # Team field resolution
        if cfg.team_field == "component":
            team = (f.get("components") or [{}])[0].get("name", "") if f.get("components") else ""
        elif cfg.team_field == "label":
            team = (f.get("labels") or [""])[0]
        else:
            # Custom field
            team_raw = f.get(cfg.team_field) or ""
            team = team_raw if isinstance(team_raw, str) else (team_raw.get("value") or team_raw.get("name") or "")

        return {
            "key": raw["key"],
            "project": (f.get("project") or {}).get("key", ""),
            "issue_type": (f.get("issuetype") or {}).get("name", ""),
            "priority": (f.get("priority") or {}).get("name", ""),
            "summary": f.get("summary", ""),
            "status": (f.get("status") or {}).get("name", ""),
            "assignee": assignee,
            "reporter": (f.get("reporter") or {}).get("displayName", ""),
            "created": f.get("created", ""),
            "resolved": f.get("resolutiondate") or "",
            "labels": labels,
            "components": components,
            "team_field": team,
        }

    def set_incremental_since(self, since_iso: str):
        """Override JQL to only fetch issues updated since a given datetime."""
        # Convert ISO datetime to Jira date format YYYY-MM-DD HH:mm
        try:
            dt = datetime.fromisoformat(since_iso.replace("Z", "+00:00"))
            since_str = dt.strftime("%Y-%m-%d %H:%M")
        except Exception:
            since_str = since_iso[:10]
        keys_clause = " OR ".join(f"project = {k}" for k in self.cfg.jira_project_keys)
        self.cfg.jira_jql_override = f'({keys_clause}) AND updated >= "{since_str}" ORDER BY updated ASC'
        log.info("Incremental JQL: %s", self.cfg.jira_jql_override)
