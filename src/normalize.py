"""Turn raw DSpace item objects into flat, chart-ready records.

Everything here is a pure function: no network, no file I/O, no config
loading. That keeps the tricky bits -- the ``dc.type`` vocabulary collapse,
date padding, and PII stripping -- fully unit-testable.
"""

from __future__ import annotations

import re
from typing import Any

# Metadata fields that must never reach the sync output, even though the API
# may return them. Filtered explicitly here rather than merely left out of the
# dashboard. Callers normally pass the list from config; this is the floor.
DEFAULT_PII_FIELDS: tuple[str, ...] = ("dc.description.provenance",)

_YEAR_RE = re.compile(r"^(\d{4})$")
_YEAR_MONTH_RE = re.compile(r"^(\d{4})-(\d{2})$")
_YEAR_MONTH_DAY_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")  # tolerates a time suffix


def first_value(
    metadata: dict[str, Any] | None, field: str, default: Any = None
) -> Any:
    """Return the first ``value`` for a metadata field, or ``default``.

    DSpace metadata is ``{field: [{"value": ...}, ...]}`` and any field may be
    absent or an empty list. This is the null-safe replacement for
    ``metadata['dc.title'][0]['value']``.
    """
    if not isinstance(metadata, dict):
        return default
    values = metadata.get(field)
    if not isinstance(values, list) or not values:
        return default
    first = values[0]
    if not isinstance(first, dict):
        return default
    got = first.get("value", default)
    return default if got is None else got


def map_type(raw_value: Any, type_map: dict[str, str], default: str) -> str:
    """Collapse a raw ``dc.type`` string to a clean category.

    Matching is exact but case- and surrounding-whitespace-insensitive so that
    ``"journal article"`` and ``"Journal Article "`` land together. Anything
    missing or unmapped becomes ``default`` -- categories are never guessed
    from substrings.
    """
    if not isinstance(raw_value, str):
        return default
    stripped = raw_value.strip()
    if not stripped:
        return default
    if stripped in type_map:
        return type_map[stripped]
    folded = {k.strip().casefold(): v for k, v in type_map.items()}
    return folded.get(stripped.casefold(), default)


def pad_issued_date(raw_value: Any) -> tuple[str | None, str | None]:
    """Normalize a ``dc.date.issued`` value to ``(YYYY-MM-DD, granularity)``.

    ``dc.date.issued`` is frequently year-only or year-month-only. Year-only
    pads to Jan 1; year-month pads to the 1st. Granularity is one of
    ``"year"``, ``"month"``, ``"day"`` so charts can fall back to yearly
    buckets and avoid clustering artifacts from the padding. Unparseable or
    missing input returns ``(None, None)``.
    """
    if not isinstance(raw_value, str):
        return (None, None)
    text = raw_value.strip()
    if (m := _YEAR_MONTH_DAY_RE.match(text)) is not None:
        y, mo, d = m.groups()
        if _valid_ymd(y, mo, d):
            return (f"{y}-{mo}-{d}", "day")
        return (None, None)
    if (m := _YEAR_MONTH_RE.match(text)) is not None:
        y, mo = m.groups()
        if _valid_ymd(y, mo, "01"):
            return (f"{y}-{mo}-01", "month")
        return (None, None)
    if (m := _YEAR_RE.match(text)) is not None:
        return (f"{m.group(1)}-01-01", "year")
    return (None, None)


def _valid_ymd(y: str, mo: str, d: str) -> bool:
    return 1 <= int(mo) <= 12 and 1 <= int(d) <= 31 and int(y) >= 1


def _date_only(raw_value: Any) -> str | None:
    """First 10 chars of an ISO timestamp (``dc.date.accessioned`` is full)."""
    if not isinstance(raw_value, str):
        return None
    text = raw_value.strip()
    if _YEAR_MONTH_DAY_RE.match(text):
        return text[:10]
    return None


def normalize_item(
    raw_item: dict[str, Any],
    *,
    department: str,
    type_map: dict[str, str],
    type_map_default: str = "Other",
    pii_fields: tuple[str, ...] | list[str] = DEFAULT_PII_FIELDS,
) -> dict[str, Any]:
    """Flatten one raw DSpace item into a reporting record.

    The ``department`` is supplied by the caller (it comes from which
    community scope the item was fetched under, per the brief's
    community-scoped resolution strategy), not read from the item.

    ``pii_fields`` are never copied out; ``type_raw`` keeps the original
    ``dc.type`` so the long tail stays auditable alongside the mapped
    category.
    """
    metadata = raw_item.get("metadata") or {}
    blocked = set(pii_fields) | set(DEFAULT_PII_FIELDS)
    safe_meta = {k: v for k, v in metadata.items() if k not in blocked}

    raw_type = first_value(safe_meta, "dc.type")
    issued, issued_granularity = pad_issued_date(
        first_value(safe_meta, "dc.date.issued")
    )

    return {
        "uuid": raw_item.get("uuid"),
        "title": first_value(safe_meta, "dc.title"),
        "department": department,
        "type": map_type(raw_type, type_map, type_map_default),
        "type_raw": raw_type,
        "date_issued": issued,
        "date_issued_granularity": issued_granularity,
        "date_accessioned": _date_only(
            first_value(safe_meta, "dc.date.accessioned")
        ),
    }
