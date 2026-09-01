# DSpace Submission Reporting Dashboard

A **public reference implementation** of an automated reporting pipeline for
[DSpace](https://dspace.org) 7+ institutional repositories. It shows how any
library can build a live submission-volume / trend dashboard using only
**Python, GitHub Actions, and static HTML** — no vendor platform, no paid
tooling, and no authentication (DSpace's REST discovery API is public by
default).

The pipeline is three moving parts:

1. `src/` — a Python job that pages through the discovery API, normalizes the
   metadata, and writes one JSON file.
2. `.github/workflows/sync.yml` — a cron-scheduled Action that runs the job.
3. `dashboard/` — a static page that renders the JSON with Chart.js.

This repo ships with **sample data only** (`data/items.example.json`). A real
institution forks it, edits one config file, and points it at their own
instance.

## Why this exists

This pattern was first proven as a Power Automate + SharePoint + Power BI
pipeline for a specific institution's repository (~20 academic departments,
1,600+ items). That version works well for an audience already living in
Microsoft 365 with SSO expectations.

This repo exists so the *underlying logic* — pagination, metadata
normalization, `dc.type` collapse, community-scoped department resolution — is
portable to institutions **without** that stack, or to anyone who wants to
understand the moving parts before committing to a platform.

## Quick start

```sh
# 1. Install (Python 3.11+)
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 2. Configure
cp config.example.yaml config.yaml
#   ... edit config.yaml (see "Configuration" below) ...

# 3. Run the sync — writes data/items.json
python -m src.sync

# 4. View the dashboard (a local server is required; file:// blocks fetch)
python -m http.server 8000
#   open http://localhost:8000/dashboard/index.html

# Run the tests
python -m pytest
```

Out of the box `config.example.yaml` points at the public
[DSpace demo](https://demo.dspace.org), so `python -m src.sync --config
config.example.yaml` works with no editing and produces real (public, non-
institutional) data to explore.

### `src.sync` options

| Flag | Default | Purpose |
| --- | --- | --- |
| `--config PATH` | `$DSPACE_DASHBOARD_CONFIG`, else `./config.yaml` | config file to load |
| `--out PATH` | `data/items.json` | where to write the dataset |
| `--dry-run` | off | fetch + normalize, print counts, write nothing |

## Configuration

Every institution-specific value lives in `config.yaml`. `src/` contains none
of them. Copy `config.example.yaml` and edit:

| Key | Required | Notes |
| --- | --- | --- |
| `base_url` | yes | DSpace server root, e.g. `https://repo.example.edu/server` (everything before `/api/...`) |
| `communities` | yes | list of `{label, uuid}`. `label` is the "department" shown in the dashboard; `uuid` is a top-level community. Find them at `<base_url>/api/core/communities/search/top` |
| `type_map` | yes | raw `dc.type` string → clean category. Matching is exact but case- and whitespace-insensitive. Build it by hand from the values your repo actually contains (see below) |
| `type_map_default` | no (`Other`) | category for any `dc.type` that is missing or unmapped |
| `pii_fields` | no (`[dc.description.provenance]`) | metadata fields stripped from output. `dc.description.provenance` is *always* stripped regardless of this list |
| `page_size` | no (`100`) | discovery page size (DSpace commonly caps at 100) |
| `request_timeout` | no (`30`) | seconds per HTTP request |
| `max_retries` | no (`3`) | extra attempts on HTTP 429 / 5xx / connection errors |

To see your `dc.type` long tail before writing `type_map`:

```
<base_url>/api/discover/search/objects?dsoType=item&size=100
```

Expect a messy mix from manual entry, batch imports, and migrations
(`Article`, `Journal Article`, `Thesis`, `Dissertation`, …) that needs to
collapse into a small set of clean categories.

## The dataset (`data/items.json`)

Full refresh each run: the file is overwritten, with one record per item UUID.

```json
{
  "generated_at": "2026-09-01T00:16:08Z",
  "source": "https://repo.example.edu/server",
  "communities": ["Chemistry", "History", "..."],
  "item_count": 297,
  "items": [
    {
      "uuid": "434d9911-fc4e-4d8f-98a1-0343a84b637a",
      "title": "…",
      "department": "Chemistry",
      "type": "Article",
      "type_raw": "Journal Article",
      "date_issued": "2008-10-01",
      "date_issued_granularity": "day",
      "date_accessioned": "2018-09-14"
    }
  ]
}
```

- `type` is the mapped category; `type_raw` keeps the original `dc.type` so the
  long tail stays auditable.
- `date_issued` is padded: year-only → January 1 (`"year"`), year-month → the
  1st (`"month"`), full date → unchanged (`"day"`). `date_issued_granularity`
  lets the dashboard fall back to yearly buckets and avoid clustering
  artifacts from the padding.
- Records are sorted by `(department, uuid)` so the committed JSON diffs
  cleanly.

## The dashboard (`dashboard/`)

Static `index.html` + `charts.js` + `style.css`. Loads `data/items.json` and
falls back to `data/items.example.json` (the only data file in the public
repo). A badge on the page always reports which file it actually loaded, so
"sample data" is only ever shown when the sample file is what rendered.

Three views: submission volume over time (with an **added vs. published** date
toggle), items by type, and items by department.

To publish it on **GitHub Pages**, serve the `dashboard/` directory. With only
`data/items.example.json` committed, the deployed page shows sample data.

## Automation (`.github/workflows/sync.yml`)

Weekly cron + manual dispatch. In **this public repo** the job runs the
pipeline against the demo instance and uploads the result as a build artifact —
it does **not** commit data back, because `data/items.json` is git-ignored and,
in a real deployment, holds institutional data that must not land in a public
repo.

To use it as your delivery mechanism in a **private fork**:

1. Provide your config — e.g. add a step that writes `config.yaml` from a
   repository secret (`printf '%s' "${{ secrets.CONFIG_YAML }}" > config.yaml`)
   and drop the `--config` flag.
2. Set repository variable `COMMIT_DATA=true` to enable the commit-back step.
3. Remove `data/items.json` from `.gitignore` (root and `data/.gitignore`) so
   it can be tracked.

## Design decisions

| Decision | Rationale |
| --- | --- |
| **Committed JSON, not SQLite** | At hundreds–low-thousands of items on a weekly sync, one JSON file is easier to inspect, diff, and explain. No binary-diff problem in git. |
| **Full refresh, not incremental** | Re-fetch everything and overwrite, keyed by UUID. Simpler than watermark/upsert tracking and cheap at this scale. A deliberate simplification vs. the production pipeline (see below). |
| **Department = community UUID scope** | One `discover/search/objects?scope=<community-uuid>` query per top-level community — `O(communities)` calls, not `O(items)` walking each item's `owningCollection` chain. |
| **Config-driven** | Community UUIDs, the `dc.type` map, and department labels all live in `config.yaml`. `src/` is institution-agnostic. |
| **Chart.js** | Lighter and more readable as example code than Plotly, for an audience that may be reading this as a learning reference. |
| **No authentication** | The discovery API is public by default. Institutions with a non-public API must add their own auth handling in `src/discover_api.py`. |
| **Sample data only** | Real institutional data and any real deployment stay out of this repo entirely — a private fork, a build-time private source, or a separate internal deployment. |

### Hazards this handles

- **Null-safe metadata.** DSpace metadata fields are arrays and missing fields
  are common; `first_value()` never assumes index 0 exists.
- **Inconsistent `dc.type`.** Collapsed via an explicit, documented dict — never
  inferred heuristically.
- **Coarse `dc.date.issued`.** Year- and month-only values are padded, and the
  granularity is recorded.
- **PII.** `dc.description.provenance` (and any configured `pii_fields`) are
  filtered in `normalize.py`, not merely omitted from the dashboard.
- **Pagination.** The client loops until a short page, rather than trusting a
  `totalPages` counter that can lag.

## How this differs from a production / enterprise deployment

This repo is a *pattern*, not a port. The production pipeline it derives from
differs in ways that are intentional here, not oversights:

| | This repo | Production (M365) pipeline |
| --- | --- | --- |
| Sync | Full refresh, overwrite | Incremental watermark upsert |
| Auth | None (public API) | SSO-gated (SharePoint / Entra) |
| Storage | Committed JSON file | SharePoint list / dataset |
| Dashboard | Static HTML + Chart.js | Power BI |
| Data | Public sample only | Live institutional data |
| Audience | Any DSpace 7+ site, no stack assumptions | Staff & department heads already in Microsoft 365 |

Institutions already in Microsoft 365 with department heads who expect
SSO-gated dashboards may still be better served by a SharePoint/Power BI
pipeline. This repo is for sites that want a lighter-weight, dependency-free
option — or that want to understand the underlying logic first.

## Out of scope

- Authentication / SSO of any kind
- Real institutional data of any kind
- Incremental / watermark sync (a possible extension, not built)
- Hosting a live institutional dashboard — that stays in each institution's own
  infrastructure
