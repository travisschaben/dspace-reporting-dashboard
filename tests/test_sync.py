"""Tests for the sync orchestration, using a fake discovery client.

No network. A ``FakeClient`` stands in for ``DSpaceClient``: it returns a
fixed list of ``(department_label, raw_item)`` pairs, so these tests cover the
wiring in ``sync.py`` -- dedup, dropping, sorting, config plumbing, output
shape, CLI exit codes -- while ``test_normalize.py`` covers per-field
correctness.
"""

from __future__ import annotations

import json
import re

import pytest

from src import sync as sync_mod
from src.config import Community, Config
from src.discover_api import DSpaceAPIError
from src.sync import _write_json, build_client, main, run_sync

_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

# Sentinel: pass as the uuid to build a raw item with no "uuid" key at all.
_OMIT = object()


def make_config(**overrides) -> Config:
    base = dict(
        base_url="https://example.test/server",
        communities=(
            Community("Physics", "uuid-phys"),
            Community("Chemistry", "uuid-chem"),
        ),
        type_map={"Journal Article": "Article", "Thesis": "Thesis"},
        type_map_default="Other",
        pii_fields=("dc.description.provenance",),
        page_size=100,
        request_timeout=30.0,
        max_retries=3,
    )
    base.update(overrides)
    return Config(**base)


def raw_item(
    uuid,
    *,
    title="A Title",
    dc_type="Journal Article",
    issued="2020-05-04",
    accessioned="2021-01-02T03:04:05Z",
    extra_metadata=None,
):
    metadata = {
        "dc.title": [{"value": title}],
        "dc.type": [{"value": dc_type}],
        "dc.date.issued": [{"value": issued}],
        "dc.date.accessioned": [{"value": accessioned}],
    }
    if extra_metadata:
        metadata.update(extra_metadata)
    item = {"metadata": metadata}
    if uuid is not _OMIT:
        item["uuid"] = uuid
    return item


class FakeClient:
    """Returns a fixed ``(label, raw_item)`` list; records the pairs it got."""

    def __init__(self, pairs):
        self.pairs = list(pairs)
        self.received_community_pairs = None

    def fetch_all(self, community_pairs):
        self.received_community_pairs = list(community_pairs)
        return list(self.pairs)


class RaisingClient:
    def fetch_all(self, community_pairs):
        raise DSpaceAPIError("boom")


# --- run_sync ------------------------------------------------------------


class TestRunSync:
    def test_envelope_shape(self):
        cfg = make_config()
        client = FakeClient(
            [("Physics", raw_item("u1")), ("Chemistry", raw_item("u2"))]
        )
        dataset, dropped = run_sync(cfg, client)

        assert set(dataset) == {
            "generated_at",
            "source",
            "communities",
            "item_count",
            "items",
        }
        assert dataset["source"] == "https://example.test/server"
        assert dataset["communities"] == ["Physics", "Chemistry"]
        assert dataset["item_count"] == 2 == len(dataset["items"])
        assert dropped == 0
        assert _TIMESTAMP_RE.match(dataset["generated_at"])

    def test_passes_configured_community_pairs_to_client(self):
        cfg = make_config()
        client = FakeClient([])
        run_sync(cfg, client)
        assert client.received_community_pairs == [
            ("Physics", "uuid-phys"),
            ("Chemistry", "uuid-chem"),
        ]

    def test_department_comes_from_fetch_pair_label(self):
        cfg = make_config()
        client = FakeClient([("Chemistry", raw_item("u1"))])
        dataset, _ = run_sync(cfg, client)
        assert dataset["items"][0]["department"] == "Chemistry"

    def test_dedup_by_uuid_last_wins(self):
        cfg = make_config()
        client = FakeClient(
            [
                ("Physics", raw_item("dup", title="first")),
                ("Chemistry", raw_item("dup", title="second")),
            ]
        )
        dataset, dropped = run_sync(cfg, client)
        assert dataset["item_count"] == 1
        assert dataset["items"][0]["title"] == "second"
        assert dropped == 0

    def test_items_without_uuid_are_dropped_and_counted(self):
        cfg = make_config()
        client = FakeClient(
            [
                ("Physics", raw_item("keep")),
                ("Physics", raw_item(None)),
                ("Physics", raw_item(_OMIT)),
            ]
        )
        dataset, dropped = run_sync(cfg, client)
        assert dropped == 2
        assert [i["uuid"] for i in dataset["items"]] == ["keep"]

    def test_items_sorted_by_department_then_uuid(self):
        cfg = make_config()
        client = FakeClient(
            [
                ("Physics", raw_item("p-b")),
                ("Chemistry", raw_item("c-z")),
                ("Physics", raw_item("p-a")),
                ("Chemistry", raw_item("c-a")),
            ]
        )
        dataset, _ = run_sync(cfg, client)
        assert [(i["department"], i["uuid"]) for i in dataset["items"]] == [
            ("Chemistry", "c-a"),
            ("Chemistry", "c-z"),
            ("Physics", "p-a"),
            ("Physics", "p-b"),
        ]

    def test_uses_config_type_map_and_default(self):
        cfg = make_config()
        client = FakeClient(
            [
                ("Physics", raw_item("u1", dc_type="Thesis")),
                ("Physics", raw_item("u2", dc_type="Wildly Unmapped")),
            ]
        )
        dataset, _ = run_sync(cfg, client)
        by_uuid = {i["uuid"]: i for i in dataset["items"]}
        assert by_uuid["u1"]["type"] == "Thesis"
        assert by_uuid["u2"]["type"] == "Other"
        assert by_uuid["u2"]["type_raw"] == "Wildly Unmapped"

    def test_uses_config_pii_fields(self):
        cfg = make_config(
            pii_fields=("dc.description.provenance", "dc.contributor.author")
        )
        client = FakeClient(
            [
                (
                    "Physics",
                    raw_item(
                        "u1",
                        extra_metadata={
                            "dc.contributor.author": [{"value": "Jane Doe"}]
                        },
                    ),
                )
            ]
        )
        dataset, _ = run_sync(cfg, client)
        assert "Jane Doe" not in json.dumps(dataset)

    def test_provenance_always_stripped_even_if_not_configured(self):
        cfg = make_config(pii_fields=())
        client = FakeClient(
            [
                (
                    "Physics",
                    raw_item(
                        "u1",
                        extra_metadata={
                            "dc.description.provenance": [
                                {"value": "Submitted by x on ..."}
                            ]
                        },
                    ),
                )
            ]
        )
        dataset, _ = run_sync(cfg, client)
        assert "provenance" not in json.dumps(dataset).lower()
        assert "Submitted by" not in json.dumps(dataset)

    def test_empty_result(self):
        cfg = make_config()
        dataset, dropped = run_sync(cfg, FakeClient([]))
        assert dataset["item_count"] == 0
        assert dataset["items"] == []
        assert dropped == 0


# --- _write_json ------------------------------------------------------------


class TestWriteJson:
    def test_creates_parent_dirs(self, tmp_path):
        out = tmp_path / "nested" / "deeper" / "items.json"
        _write_json({"item_count": 0, "items": []}, out)
        assert out.is_file()

    def test_trailing_newline_and_valid_json(self, tmp_path):
        out = tmp_path / "items.json"
        _write_json({"item_count": 1, "items": [{"uuid": "u"}]}, out)
        text = out.read_text(encoding="utf-8")
        assert text.endswith("\n")
        assert json.loads(text)["items"][0]["uuid"] == "u"

    def test_overwrites_existing_file(self, tmp_path):
        out = tmp_path / "items.json"
        out.write_text("stale garbage", encoding="utf-8")
        _write_json({"item_count": 0, "items": []}, out)
        assert json.loads(out.read_text(encoding="utf-8"))["item_count"] == 0

    def test_leaves_no_tmp_sibling(self, tmp_path):
        out = tmp_path / "items.json"
        _write_json({"item_count": 0, "items": []}, out)
        assert list(tmp_path.iterdir()) == [out]

    def test_non_ascii_preserved(self, tmp_path):
        out = tmp_path / "items.json"
        _write_json({"items": [{"title": "Étude café"}]}, out)
        assert "Étude café" in out.read_text(encoding="utf-8")


# --- build_client ------------------------------------------------------------


def test_build_client_maps_config_fields():
    cfg = make_config(page_size=25, request_timeout=5.0, max_retries=1)
    client = build_client(cfg)
    assert client.base_url == "https://example.test/server"
    assert client.page_size == 25
    assert client.timeout == 5.0
    assert client.max_retries == 1


# --- main / CLI ------------------------------------------------------------


@pytest.fixture
def patched(monkeypatch):
    """Patch load_config + build_client; expose a settable FakeClient."""

    holder = {"client": FakeClient([("Physics", raw_item("u1"))]), "cfg": make_config()}
    monkeypatch.setattr(sync_mod, "load_config", lambda path=None: holder["cfg"])
    monkeypatch.setattr(sync_mod, "build_client", lambda cfg: holder["client"])
    return holder


class TestMain:
    def test_writes_file_and_returns_zero(self, patched, tmp_path):
        out = tmp_path / "data" / "items.json"
        rc = main(["--out", str(out)])
        assert rc == 0
        payload = json.loads(out.read_text(encoding="utf-8"))
        assert payload["item_count"] == 1
        assert list(out.parent.iterdir()) == [out]  # no .tmp left

    def test_dry_run_writes_nothing(self, patched, tmp_path):
        out = tmp_path / "items.json"
        rc = main(["--out", str(out), "--dry-run"])
        assert rc == 0
        assert not out.exists()

    def test_dry_run_still_fetches(self, patched, tmp_path):
        main(["--out", str(tmp_path / "x.json"), "--dry-run"])
        assert patched["client"].received_community_pairs == [
            ("Physics", "uuid-phys"),
            ("Chemistry", "uuid-chem"),
        ]

    def test_missing_config_returns_2(self, tmp_path, capsys):
        rc = main(["--config", str(tmp_path / "nope.yaml"), "--out", str(tmp_path / "o.json")])
        assert rc == 2
        assert "config error" in capsys.readouterr().err

    def test_api_error_returns_1(self, patched, tmp_path, capsys):
        patched["client"] = RaisingClient()
        rc = main(["--out", str(tmp_path / "o.json")])
        assert rc == 1
        assert "sync failed" in capsys.readouterr().err
        assert not (tmp_path / "o.json").exists()

    def test_summary_reports_dropped(self, patched, tmp_path, capsys):
        patched["client"] = FakeClient(
            [("Physics", raw_item("u1")), ("Physics", raw_item(None))]
        )
        main(["--out", str(tmp_path / "o.json")])
        assert "1 skipped (no uuid)" in capsys.readouterr().err
