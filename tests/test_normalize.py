"""Unit tests for the dc.type mapping, date padding, and PII stripping."""

from __future__ import annotations

import pytest

from src.normalize import (
    first_value,
    map_type,
    normalize_item,
    pad_issued_date,
)

TYPE_MAP = {
    "Article": "Article",
    "Journal Article": "Article",
    "Thesis": "Thesis",
    "Dissertation": "Thesis",
}


# --- first_value ------------------------------------------------------------


def _md(field: str, value):
    return {field: [{"value": value, "language": None, "authority": None}]}


class TestFirstValue:
    def test_returns_first_value(self):
        assert first_value(_md("dc.title", "Hello"), "dc.title") == "Hello"

    def test_missing_field_returns_default(self):
        assert first_value(_md("dc.title", "x"), "dc.type", "fallback") == "fallback"

    def test_empty_list_returns_default(self):
        assert first_value({"dc.type": []}, "dc.type", "fallback") == "fallback"

    def test_element_without_value_key_returns_default(self):
        assert first_value({"dc.type": [{"language": None}]}, "dc.type") is None

    def test_explicit_null_value_returns_default(self):
        assert first_value(_md("dc.type", None), "dc.type", "fallback") == "fallback"

    def test_metadata_not_a_dict(self):
        assert first_value(None, "dc.type", "fallback") == "fallback"
        assert first_value([], "dc.type", "fallback") == "fallback"


# --- map_type ------------------------------------------------------------


class TestMapType:
    def test_known_value(self):
        assert map_type("Journal Article", TYPE_MAP, "Other") == "Article"

    def test_unknown_value_falls_back_to_default(self):
        assert map_type("Blog Post", TYPE_MAP, "Other") == "Other"

    def test_none_falls_back_to_default(self):
        assert map_type(None, TYPE_MAP, "Other") == "Other"

    def test_empty_string_falls_back_to_default(self):
        assert map_type("   ", TYPE_MAP, "Other") == "Other"

    def test_case_and_whitespace_insensitive(self):
        assert map_type("  journal article ", TYPE_MAP, "Other") == "Article"
        assert map_type("THESIS", TYPE_MAP, "Other") == "Thesis"

    def test_non_string_input(self):
        assert map_type(42, TYPE_MAP, "Other") == "Other"

    def test_no_heuristic_substring_match(self):
        # "Article Review" contains "Article" but is not an exact key.
        assert map_type("Article Review", TYPE_MAP, "Other") == "Other"


# --- pad_issued_date ------------------------------------------------------------


class TestPadIssuedDate:
    def test_year_only(self):
        assert pad_issued_date("2021") == ("2021-01-01", "year")

    def test_year_month(self):
        assert pad_issued_date("2021-05") == ("2021-05-01", "month")

    def test_full_date(self):
        assert pad_issued_date("2021-05-09") == ("2021-05-09", "day")

    def test_iso_timestamp_truncated_to_day(self):
        assert pad_issued_date("2021-05-09T12:30:00Z") == ("2021-05-09", "day")

    def test_surrounding_whitespace(self):
        assert pad_issued_date("  2021  ") == ("2021-01-01", "year")

    @pytest.mark.parametrize("bad", ["", "   ", "n.d.", "undated", "20xx", None, 2021])
    def test_unparseable_returns_none_pair(self, bad):
        assert pad_issued_date(bad) == (None, None)

    def test_impossible_month_rejected(self):
        assert pad_issued_date("2021-13") == (None, None)

    def test_impossible_day_rejected(self):
        assert pad_issued_date("2021-05-40") == (None, None)


# --- normalize_item ------------------------------------------------------------


def _raw_item(**meta_pairs):
    metadata: dict = {}
    for field, value in meta_pairs.items():
        metadata[field.replace("__", ".")] = [{"value": value}]
    return {"uuid": "abc-123", "name": "n", "metadata": metadata}


class TestNormalizeItem:
    def test_flattens_expected_fields(self):
        raw = _raw_item(
            dc__title="A Study",
            dc__type="Journal Article",
            dc__date__issued="2020",
            dc__date__accessioned="2021-02-03T04:05:06Z",
        )
        rec = normalize_item(raw, department="Physics", type_map=TYPE_MAP)
        assert rec == {
            "uuid": "abc-123",
            "title": "A Study",
            "department": "Physics",
            "type": "Article",
            "type_raw": "Journal Article",
            "date_issued": "2020-01-01",
            "date_issued_granularity": "year",
            "date_accessioned": "2021-02-03",
        }

    def test_department_comes_from_argument_not_item(self):
        rec = normalize_item(
            _raw_item(dc__title="x"), department="Chemistry", type_map=TYPE_MAP
        )
        assert rec["department"] == "Chemistry"

    def test_provenance_is_stripped_even_if_present(self):
        raw = _raw_item(
            dc__title="x",
            dc__description__provenance="Submitted by badactor (a@b.c) on ...",
        )
        rec = normalize_item(raw, department="D", type_map=TYPE_MAP)
        assert "provenance" not in repr(rec)
        assert "badactor" not in repr(rec)

    def test_extra_configured_pii_field_is_stripped(self):
        raw = _raw_item(dc__title="x", dc__contributor__author="Jane Doe")
        rec = normalize_item(
            raw,
            department="D",
            type_map=TYPE_MAP,
            pii_fields=["dc.contributor.author"],
        )
        assert "Jane Doe" not in repr(rec)

    def test_raw_type_preserved_alongside_mapped(self):
        raw = _raw_item(dc__title="x", dc__type="Some Unmapped Type")
        rec = normalize_item(raw, department="D", type_map=TYPE_MAP)
        assert rec["type"] == "Other"
        assert rec["type_raw"] == "Some Unmapped Type"

    def test_missing_type_and_dates_are_none(self):
        rec = normalize_item(
            _raw_item(dc__title="x"), department="D", type_map=TYPE_MAP
        )
        assert rec["type"] == "Other"
        assert rec["type_raw"] is None
        assert rec["date_issued"] is None
        assert rec["date_issued_granularity"] is None
        assert rec["date_accessioned"] is None

    def test_item_with_no_metadata_key(self):
        rec = normalize_item(
            {"uuid": "u1"}, department="D", type_map=TYPE_MAP
        )
        assert rec["uuid"] == "u1"
        assert rec["title"] is None
