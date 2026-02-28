"""
src/cache.py — local JSON cache for Jira issues and changelogs.

How it works:
  - First run: fetches everything, saves to .cache/ folder
  - Subsequent runs: only fetches issues updated since last sync
  - Changelogs: only fetched for new/updated issues
  - Cache is invalidated if config changes (JQL, project keys, backfill_from)
"""

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

CACHE_DIR = ".cache"
ISSUES_FILE = os.path.join(CACHE_DIR, "issues.json")
CHANGELOGS_FILE = os.path.join(CACHE_DIR, "changelogs.json")
META_FILE = os.path.join(CACHE_DIR, "meta.json")


def _config_hash(cfg) -> str:
    """Hash the config so we can detect if it changed."""
    key = f"{cfg.jira_base_url}|{cfg.jira_project_keys}|{cfg.backfill_from}|{getattr(cfg, 'jira_jql_override', '')}"
    return hashlib.md5(key.encode()).hexdigest()[:8]


def load_cache(cfg) -> Tuple[Optional[List[Dict]], Optional[Dict[str, List]], Optional[str]]:
    """
    Returns (issues, changelogs, last_sync_dt_str) or (None, None, None) if no valid cache.
    last_sync_dt_str is an ISO datetime string to use for incremental JQL.
    """
    if not os.path.exists(META_FILE):
        log.info("No cache found — will do full fetch")
        return None, None, None

    with open(META_FILE) as f:
        meta = json.load(f)

    # Invalidate if config changed
    if meta.get("config_hash") != _config_hash(cfg):
        log.info("Config changed — invalidating cache, will do full fetch")
        _clear_cache()
        return None, None, None

    if not os.path.exists(ISSUES_FILE) or not os.path.exists(CHANGELOGS_FILE):
        log.info("Cache files missing — will do full fetch")
        return None, None, None

    log.info("Loading cache from %s (last sync: %s)", CACHE_DIR, meta.get("last_sync"))

    with open(ISSUES_FILE) as f:
        issues = json.load(f)

    with open(CHANGELOGS_FILE) as f:
        changelogs = json.load(f)

    log.info("Cache loaded: %d issues, %d changelogs", len(issues), len(changelogs))
    return issues, changelogs, meta.get("last_sync")


def save_cache(cfg, issues: List[Dict], changelogs: Dict[str, List]):
    """Save issues and changelogs to local cache."""
    os.makedirs(CACHE_DIR, exist_ok=True)

    with open(ISSUES_FILE, "w") as f:
        json.dump(issues, f)

    with open(CHANGELOGS_FILE, "w") as f:
        json.dump(changelogs, f)

    meta = {
        "last_sync": datetime.now(timezone.utc).isoformat(),
        "config_hash": _config_hash(cfg),
        "issue_count": len(issues),
        "changelog_count": len(changelogs),
    }
    with open(META_FILE, "w") as f:
        json.dump(meta, f, indent=2)

    log.info("Cache saved: %d issues, %d changelogs", len(issues), len(changelogs))


def merge_updated_issues(
    cached_issues: List[Dict],
    updated_issues: List[Dict],
    updated_changelogs: Dict[str, List],
    cached_changelogs: Dict[str, List],
) -> Tuple[List[Dict], Dict[str, List]]:
    """
    Merge newly fetched issues/changelogs into the cached set.
    Updated issues replace their cached counterparts by key.
    """
    updated_keys = {i["key"] for i in updated_issues}

    # Replace updated issues in cache
    merged_issues = [i for i in cached_issues if i["key"] not in updated_keys] + updated_issues

    # Merge changelogs — updated ones replace cached
    merged_changelogs = {**cached_changelogs, **updated_changelogs}

    log.info(
        "Merged: %d updated issues into %d cached → %d total",
        len(updated_issues), len(cached_issues), len(merged_issues)
    )
    return merged_issues, merged_changelogs


def _clear_cache():
    for f in [ISSUES_FILE, CHANGELOGS_FILE, META_FILE]:
        if os.path.exists(f):
            os.remove(f)
    log.info("Cache cleared")
