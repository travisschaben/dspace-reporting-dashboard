"""Fetch every configured community's items, normalize them, write one JSON file.

This is the pipeline entry point:

    python -m src.sync                        # uses ./config.yaml -> data/items.json
    python -m src.sync --config other.yaml    # prove portability against another config
    python -m src.sync --dry-run              # fetch + normalize, report counts, write nothing

Sync strategy is a **full refresh**: every run re-fetches all items and
overwrites the output file. Records are merged by item UUID, so the output has
one entry per item regardless of how many community scopes it appeared in.
This is a deliberate simplification over an incremental/watermark upsert; at a
few thousand items on a weekly cron it is cheap and far easier to reason about.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from src.config import Config, ConfigError, load_config
from src.discover_api import DSpaceAPIError, DSpaceClient
from src.normalize import normalize_item

DEFAULT_OUTPUT = "data/items.json"


def build_client(cfg: Config) -> DSpaceClient:
    return DSpaceClient(
        cfg.base_url,
        page_size=cfg.page_size,
        timeout=cfg.request_timeout,
        user_agent=cfg.user_agent,
        max_retries=cfg.max_retries,
    )


def run_sync(
    cfg: Config, client: DSpaceClient | None = None
) -> tuple[dict, int]:
    """Return ``(dataset, dropped)``.

    ``dataset`` is the JSON-serializable envelope; ``dropped`` counts items
    skipped because they had no UUID (nothing to key on).
    """
    client = client or build_client(cfg)
    raw_pairs = client.fetch_all(cfg.community_pairs())

    by_uuid: dict[str, dict] = {}
    dropped = 0
    for label, raw in raw_pairs:
        record = normalize_item(
            raw,
            department=label,
            type_map=cfg.type_map,
            type_map_default=cfg.type_map_default,
            pii_fields=cfg.pii_fields,
        )
        uid = record.get("uuid")
        if not uid:
            dropped += 1
            continue
        by_uuid[uid] = record  # last scope wins; fetch_all already de-duped

    # Stable ordering keeps git diffs of the committed JSON legible.
    items = sorted(by_uuid.values(), key=lambda r: (r["department"], r["uuid"]))
    dataset = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": cfg.base_url,
        "communities": [c.label for c in cfg.communities],
        "item_count": len(items),
        "items": items,
    }
    return dataset, dropped


def _write_json(dataset: dict, out_path: Path) -> None:
    """Write via a temp file + atomic replace so a crash can't truncate output."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_name(out_path.name + ".tmp")
    tmp.write_text(
        json.dumps(dataset, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    tmp.replace(out_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m src.sync", description=__doc__.splitlines()[0]
    )
    parser.add_argument(
        "--config",
        metavar="PATH",
        help="config file (default: $DSPACE_DASHBOARD_CONFIG or ./config.yaml)",
    )
    parser.add_argument(
        "--out",
        metavar="PATH",
        default=DEFAULT_OUTPUT,
        help=f"output JSON path (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="fetch and normalize, print a summary, write nothing",
    )
    args = parser.parse_args(argv)

    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    try:
        dataset, dropped = run_sync(cfg)
    except DSpaceAPIError as exc:
        print(f"sync failed: {exc}", file=sys.stderr)
        return 1

    summary = (
        f"{dataset['item_count']} items from "
        f"{len(cfg.communities)} communities ({cfg.base_url})"
    )
    if dropped:
        summary += f"; {dropped} skipped (no uuid)"
    print(summary, file=sys.stderr)

    if args.dry_run:
        print("dry run: nothing written", file=sys.stderr)
        return 0

    out_path = Path(args.out)
    _write_json(dataset, out_path)
    print(f"wrote {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
