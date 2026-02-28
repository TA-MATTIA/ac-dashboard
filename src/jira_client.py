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
    "labels", "components", "customfield_10059",
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

    def fetch_issues(self) -> List[Dict]:
        jql = self._build_jql()
        log.info("JQL: %s", jql)
        issues = []
        for page in self._paginate_search(jql):
            issues.extend(page)
        return issues

    def fetch_changelogs(self, issues: List[Dict]) -> Dict[str, List[Dict]]:
        changelogs: Dict[str, List[Dict]] = {}
        total = len(issues)
        for idx, issue in enumerate(issues, 1):
            key = issue["key"]
            if idx % 50 == 0 or idx == total:
                log.info("  Changelogs: %d / %d", idx, total)
            changelogs[key] = list(self._fetch_issue_changelog(key))
        return changelogs

    def _build_jql(self) -> str:
        cfg = self.cfg
        if cfg.jira_jql_override:
            return cfg.jira_jql_override
        keys_clause = " OR ".join(f"project = {k}" for k in cfg.jira_project_keys)
        return f"({keys_clause}) AND updated >= \"{cfg.backfill_from}\" ORDER BY updated ASC"

    def _paginate_search(self, jql: str) -> Generator[List[Dict], None, None]:
        page_size = self.cfg.page_size
        next_page_token = None
        while True:
            params = {
                "jql": jql,
                "maxResults": page_size,
                "fields": ",".join(FIELDS),
            }
            if next_page_token:
                params["nextPageToken"] = next_page_token
            data = self._get(
                f"{self.base}/rest/api/3/search/jql",
                params=params,
            )
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

    def _get(self, url: str, params: Optional[Dict] = None) -> Dict:
        backoff = 1.0
        for attempt in range(self.cfg.max_retries):
            try:
                resp = self.session.get(url, params=params, timeout=30)
                if resp.status_code == 429:
                    retry_after = float(resp.headers.get("Retry-After", backoff))
                    log.warning("Rate-limited. Sleeping %.1fs ...", retry_after)
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
                log.warning("Request error: %s — retrying ...", exc)
                time.sleep(backoff)
                backoff *= self.cfg.retry_backoff
        raise RuntimeError(f"Exceeded max retries for {url}")

    def _flatten_issue(self, raw: Dict) -> Dict:
        f = raw.get("fields", {})
        cfg = self.cfg
        assignee = (f.get("assignee") or {}).get("displayName", "")
        components = ",".join(c["name"] for c in (f.get("components") or []))
        labels = ",".join(f.get("labels") or [])
        if cfg.team_field == "component":
            team = (f.get("components") or [{}])[0].get("name", "") if f.get("components") else ""
        elif cfg.team_field == "label":
            team = (f.get("labels") or [""])[0]
        else:
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