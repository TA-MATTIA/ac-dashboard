# Jira Movement Reporter

Pulls Jira issue changelogs daily and writes KPI tables + raw data to a shared Google Sheet.  
Multiple people can view the Sheet; pivot tables and charts live in the `dashboard` tab.

---

## Repo Structure

```
jira-movement-reporter/
├── jira_sheet_sync.py          # entry point
├── config.yaml                 # all settings (secrets via env vars)
├── requirements.txt
├── setup_cron.sh               # optional: install a system cron job
├── service_account.json        # NOT committed — you create this (see setup)
├── src/
│   ├── config.py               # config loader
│   ├── jira_client.py          # Jira REST API v3 client
│   ├── changelog_parser.py     # derives movement_events from changelogs
│   ├── metrics.py              # computes all KPIs
│   └── sheets_writer.py        # writes all 6 tabs to Google Sheets
└── .github/
    └── workflows/
        └── daily_sync.yml      # GitHub Actions — runs every day at 06:00 UTC
```

---

## Google Sheet Layout

### Tab 1 — `config`
Pre-populated by the script on first run. Edit values here to change behaviour without redeploying.

| Column A (Setting)    | Column B (Value)          | Column C (Notes)           |
|-----------------------|---------------------------|----------------------------|
| jira_base_url         | https://yourco.atlassian… | Your Jira Cloud URL        |
| project_keys          | PROJ,ENG                  | Comma-separated            |
| backfill_days         | 90                        | How far back to pull       |
| in_progress_statuses  | in progress,in review     | Statuses = In Progress     |
| done_statuses         | done,closed,resolved      | Statuses = Done            |
| team_field            | component                 | component/label/fieldid    |
| last_run              | (auto-filled)             |                            |

### Tab 2 — `raw_issues_snapshot`
Latest snapshot of every issue. Replaced on each run.

`key | project | issue_type | priority | summary | status | assignee | reporter | created | resolved | labels | components | team_field`

### Tab 3 — `raw_changelog_snapshot`
All changelog items (any field). Replaced on each run.

`issue_key | changelog_id | changed_at | changed_by | field | from_value | to_value`

### Tab 4 — `movement_events` ⭐ (core table)
Status transitions only. **Append-only, deduplicated by `event_id`.**

| Column           | Description                                      |
|------------------|--------------------------------------------------|
| `event_id`       | SHA-256 hash of (key+timestamp+from+to) — 16 chars |
| `issue_key`      | e.g. PROJ-123                                    |
| `project`        | project key                                      |
| `issue_type`     | Story / Bug / Task …                             |
| `priority`       | Highest / High / Medium / Low                    |
| `created`        | Issue creation timestamp                         |
| `resolved`       | Issue resolution timestamp                       |
| `from_status`    | Status before transition                         |
| `to_status`      | Status after transition                          |
| `changed_at`     | Transition timestamp (ISO 8601)                  |
| `changed_by`     | Jira user who made the change                    |
| `assignee`       | Assignee at time of sync                         |
| `labels`         | Comma-separated labels                           |
| `components`     | Comma-separated components                       |
| `team_field`     | Resolved team grouping value                     |
| `current_status` | Issue status at last sync                        |
| `current_assignee` | Assignee at last sync                          |

### Tab 5 — `metrics`
Pre-computed KPI tables, stacked vertically, replaced on each run.

Tables (each preceded by a section header row):
- **THROUGHPUT** — weekly count of tickets moved into Done
- **CYCLE_TIME** — avg/p50/p90 cycle and lead time (overall + by assignee + by team)
- **WIP** — current count of open issues by status
- **AGING_WIP** — open issues stuck >7d / >14d / >30d in their current status
- **REOPEN_RATE** — weekly done count, reopen count, and reopen %
- **TIME_IN_STATUS** — avg/p50/p90 hours spent in each status

### Tab 6 — `dashboard`
Home for all charts and pivot tables. Never touched by the script.

---

## Setup Instructions

### Step 1 — Jira API Token

1. Go to https://id.atlassian.com/manage-profile/security/api-tokens
2. Click **Create API token** → give it a name → copy the token
3. You'll need: your Jira site URL, your Atlassian account email, and this token

### Step 2 — Google Service Account

1. Go to https://console.cloud.google.com
2. Create a new project (or use an existing one)
3. Enable the **Google Sheets API** and **Google Drive API**
4. Go to **IAM & Admin → Service Accounts** → Create Service Account
   - Name it anything (e.g. `jira-reporter`)
   - No roles needed at project level
5. Click the service account → **Keys** tab → **Add Key → JSON**
   - Download the JSON file and save it as `service_account.json` in the repo root
   - **Never commit this file** (it's in `.gitignore`)
6. Copy the service account's email address (looks like `jira-reporter@project-id.iam.gserviceaccount.com`)

### Step 3 — Create and Share the Google Sheet

1. Go to https://sheets.google.com and create a new blank sheet
2. Name it `Jira Movement Report` (or anything you like)
3. Click **Share** and paste the service account email → give it **Editor** access
4. Copy the Sheet ID from the URL:
   `https://docs.google.com/spreadsheets/d/THIS_IS_THE_ID/edit`

### Step 4 — Configure

Edit `config.yaml`:
```yaml
jira:
  base_url: "https://yourcompany.atlassian.net"
  email: "you@yourcompany.com"
  project_keys:
    - "PROJ"
    - "ENG"

statuses:
  in_progress: ["In Progress", "In Review", "In Development"]
  done: ["Done", "Closed", "Resolved"]

team:
  field: "component"    # or "label" or "customfield_10050"

backfill_days: 90
```

Set secrets as environment variables (recommended) or directly in config.yaml (not recommended for tokens):
```bash
export JIRA_API_TOKEN="your-jira-api-token"
export GOOGLE_SHEET_ID="your-google-sheet-id"
```

### Step 5 — Install & Run

```bash
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt

# Test with dry run first
python jira_sheet_sync.py --dry-run

# Real run
python jira_sheet_sync.py
```

On first run the script creates all 6 tabs and does a full backfill.

---

## Scheduling

### Option A — GitHub Actions (recommended)

1. Push this repo to GitHub
2. Go to **Settings → Secrets and variables → Actions → New repository secret**
   Add these secrets:
   - `JIRA_BASE_URL`
   - `JIRA_EMAIL`
   - `JIRA_API_TOKEN`
   - `GOOGLE_SHEET_ID`
   - `GOOGLE_SERVICE_ACCOUNT_JSON` — paste the entire contents of `service_account.json`
3. The workflow in `.github/workflows/daily_sync.yml` runs automatically at 06:00 UTC
4. You can also trigger it manually from the **Actions** tab → **Run workflow**

### Option B — System Cron

```bash
chmod +x setup_cron.sh
./setup_cron.sh
```

Or add manually to crontab (`crontab -e`):
```
0 6 * * * cd /path/to/jira-movement-reporter && source .env && python jira_sheet_sync.py >> logs/sync.log 2>&1
```

---

## Dashboard Setup — Pivot Tables & Charts

After the first successful run, follow these steps inside the Google Sheet.

### Pivot 1 — Weekly Throughput (Bar Chart)

1. In the `dashboard` tab, click a blank cell (e.g. A1)
2. **Insert → Pivot table** → Data range: `metrics` tab rows containing the THROUGHPUT table
   - Better approach: Insert → Chart → from `metrics` tab  
3. Select the THROUGHPUT rows (week + tickets_done columns)
4. **Insert → Chart** → Chart type: **Column chart**
   - X-axis: `week`
   - Series: `tickets_done`
   - Title: "Weekly Throughput — Tickets Done"

### Pivot 2 — Cycle Time by Assignee (Table)

1. In `dashboard`, click cell A20
2. **Insert → Pivot table** → source: `metrics` tab, CYCLE_TIME section
3. Rows: `group` | Values: `cycle_avg_h`, `cycle_p50_h`, `cycle_p90_h`
4. Add a Bar chart from this pivot:
   - Title: "Cycle Time by Assignee (hours)"

### Pivot 3 — WIP by Status (Donut / Pie Chart)

1. Select the WIP section in the `metrics` tab
2. **Insert → Chart** → Chart type: **Donut chart**
   - Dimension: `status`
   - Metric: `wip_count`
   - Title: "Current WIP by Status"

### Pivot 4 — Aging WIP (Stacked Bar)

1. In `dashboard`, click cell A50
2. **Insert → Pivot table** → source: `metrics` AGING_WIP section
3. Rows: `bucket` | Columns: `current_status` | Values: COUNTA of `issue_key`
4. Insert → Chart → **Stacked bar chart**
   - Title: "Aging WIP — Tickets Stuck in Status"

### Pivot 5 — Reopen Rate (Combo Chart)

1. Select REOPEN_RATE section from `metrics`
2. **Insert → Chart** → Chart type: **Combo chart**
   - Bars: `tickets_done` and `reopens`
   - Line: `reopen_rate_pct` (right Y-axis)
   - Title: "Weekly Reopen Rate"

### Pivot 6 — Time in Status (Horizontal Bar)

1. Select TIME_IN_STATUS section
2. **Insert → Chart** → **Bar chart** (horizontal)
   - Y-axis: `status`
   - Series: `avg_hours`, `p50_hours`, `p90_hours`
   - Title: "Time in Status (hours)"

### Sharing the Dashboard

1. Click **Share** (top right of the Sheet)
2. Add teammates by email with **Viewer** access (they can see charts but not edit data)
3. Or click **Share → Anyone with the link → Viewer** for read-only public access

---

## Environment Variables Reference

| Variable                    | Description                              |
|-----------------------------|------------------------------------------|
| `JIRA_BASE_URL`             | `https://yourco.atlassian.net`           |
| `JIRA_EMAIL`                | Your Atlassian account email             |
| `JIRA_API_TOKEN`            | API token from id.atlassian.com          |
| `GOOGLE_SHEET_ID`           | Sheet ID from the Google Sheets URL      |
| `GOOGLE_SERVICE_ACCOUNT_FILE` | Path to service account JSON file      |

---

## Dry Run Mode

```bash
python jira_sheet_sync.py --dry-run
```

Prints a summary of what would be written without touching the Sheet. Useful for testing config changes.

---

## Customisation Tips

**Different "In Progress" statuses per project?**  
Add a `jql_override` in config.yaml per project and run the script multiple times with different configs.

**Custom team field (e.g. Jira team field)**  
Set `team.field` to the custom field ID (e.g. `customfield_10050`). Find the field ID via:
`GET https://yourco.atlassian.net/rest/api/3/field`

**Epics / sprints in the Sheet?**  
Add `customfield_10014` (epic link) and `customfield_10020` (sprint) to the `FIELDS` list in `src/jira_client.py` and extend `_flatten_issue`.

**Jira Service Management**  
Set `jira.type: service_management` in config.yaml. The script works identically; this is just a label for future conditional logic.
