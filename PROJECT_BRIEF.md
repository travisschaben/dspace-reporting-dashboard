# DSpace Submission Reporting Dashboard — Reference Implementation

## Project purpose

This is a **public reference implementation** of an automated reporting pipeline for DSpace institutional repositories. It shows how any library running DSpace 7+ can build a live submission-volume/trend dashboard using only Python, GitHub Actions, and static HTML — no vendor platform, no paid tooling, no authentication required (since DSpace's REST discovery API is public by default).

This repo is deliberately separate from any production deployment. It ships with **sample data only**. A real institution forks it, edits one config file, and points it at their own DSpace instance.

Context: this pattern was proven out first as a Power Automate + SharePoint + Power BI pipeline for a specific institution's repository (20 academic departments, ~1,600+ items). That version works well for an audience already living in Microsoft 365 with SSO expectations. This repo exists so the *underlying logic* — pagination, metadata normalization, department resolution — is portable to institutions without that stack.

## Repo layout

```
dspace-reporting-dashboard/
├── .github/
│   └── workflows/
│       └── sync.yml              # cron-triggered Actions workflow
├── src/
│   ├── sync.py                   # main entrypoint: fetch → normalize → write JSON
│   ├── discover_api.py           # pagination, community-scoped queries
│   ├── normalize.py              # dc.type → clean category mapping, date padding
│   └── config.py                 # loads config.yaml, exposes typed settings
├── data/
│   ├── items.example.json        # committed sample data (fake/anonymized records)
│   └── .gitignore                # ignores items.json (real data, if ever run locally)
├── dashboard/
│   ├── index.html
│   ├── charts.js                 # Chart.js rendering, reads data/items.json (falls back to items.example.json)
│   └── style.css
├── config.example.yaml           # copy to config.yaml and fill in your instance details
├── README.md                     # setup steps + the "why this exists" pitch
├── requirements.txt
└── tests/
    └── test_normalize.py         # unit tests for dc.type mapping and date padding logic
```

## Core architectural decisions (locked in)

- **Storage: committed JSON, not SQLite.** At the scale this targets (hundreds to low thousands of items, weekly sync), a single JSON file is simpler to inspect, diff, and explain than a database. No binary-diff problem in git.
- **Sync strategy: full refresh, not incremental.** Re-fetch all items each run and overwrite `data/items.json`, keyed by item UUID. Simpler than watermark/incremental tracking, and cheap at this scale. (Note: this is a deliberate simplification vs. the production pipeline's incremental upsert pattern — call this out in the README as a scale tradeoff, not an oversight.)
- **Department resolution: scoped by community UUID.** Query `/server/api/discover/search/objects?scope=<community-uuid>` per top-level community rather than walking each item's `owningCollection` chain. O(communities) API calls instead of O(items).
- **Config-driven, not hardcoded.** Community UUIDs, the dc.type → category mapping, and department list all live in `config.yaml`. `sync.py` and `normalize.py` should contain zero institution-specific values.
- **Charting: Chart.js.** Lighter weight and more readable as example code than Plotly, for an audience of library staff who may be reading this as a learning reference, not just running it.
- **Hosting: GitHub Pages, public repo, sample data only.** Real institutional data and any real deployment stay out of this repo entirely — either a private fork, a private data source consumed at build time, or a separate internal deployment. This repo should never contain real patron- or institution-sensitive data.
- **No authentication required.** DSpace's discovery API is public by default; this pipeline assumes that and doesn't build any auth layer. Note in the README that institutions with a non-public API will need to add their own auth handling.

## Known hazards to design around (learned the hard way on the production version)

- **Null-safe metadata access.** DSpace metadata fields are arrays; missing fields are common. Always guard with something like `metadata.get('dc.type', [{}])[0].get('value')`, never assume index 0 exists.
- **`dc.type` vocabulary is inconsistent.** Expect a long tail of raw values from mixed input paths (manual entry, batch import, etc.) that need to collapse into a small set of clean categories. Build the mapping as an explicit, documented dict in config — don't try to infer categories heuristically.
- **`dc.date.issued` (published date) is often year-only or year-month-only.** Pad year-only to January 1, year-month to the 1st of that month. When charting by published date, default to yearly granularity to avoid artificial clustering artifacts from the padding.
- **PII fields are a hard boundary.** Fields like `dc.description.provenance` should never be read into the sync output, even though the API technically returns them. Filter them out explicitly in `normalize.py`, not just by omission in the dashboard.
- **Pagination.** DSpace discovery responses are paginated; loop until a page returns fewer than the page size, don't rely on a total-pages field that may not update as expected.

## Suggested build order

1. **`discover_api.py`** — API client with pagination against a live public DSpace instance (test against a real open instance, e.g. a well-known DSpace repo, before wiring in any specific institution's data).
2. **`normalize.py`** — dc.type mapping, date padding, PII field stripping. Write unit tests alongside this before moving on.
3. **`sync.py`** — wire client + normalizer together, write `data/items.json`, run once locally end-to-end against sample/test data.
4. **`config.yaml` / `config.example.yaml`** — extract every institution-specific value out of the code and into config; confirm `sync.py` runs against a *different* fake config to prove portability.
5. **`.github/workflows/sync.yml`** — cron schedule, checkout, run `sync.py`, commit updated `data/items.json` back to the repo (skip this step in the public repo if real data should never land there — see hosting note above).
6. **`dashboard/`** — static HTML + Chart.js reading the JSON: submission volume over time, item-type breakdown, department breakdown, with a date-view toggle (accessioned vs. published).
7. **`README.md`** — the pitch, setup steps, and a short "how this differs from a production/enterprise deployment" section.
8. **GitHub Pages deployment** — publish `dashboard/` from the public repo using only `items.example.json`.

## Explicitly out of scope for this repo

- Authentication/SSO of any kind
- Real institutional data of any kind
- Incremental/watermark sync (documented as a possible extension, not built)
- Any hosting/deployment of a live institutional dashboard — that stays in each institution's own infrastructure

## Relationship to the production pipeline

This repo is a *pattern*, not a port. Institutions with Microsoft 365 already available and department heads who expect SSO-gated dashboards may still be better served by a SharePoint/Power BI-style pipeline. This repo is for institutions that want a lighter-weight, dependency-free option, or that want to understand the underlying logic before choosing a platform.
