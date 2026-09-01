"""Load ``config.yaml`` and expose it as typed settings.

Every institution-specific value -- server URL, community UUIDs, the
``dc.type`` mapping, the department list -- lives in the YAML file. ``sync.py``
and ``normalize.py`` import from here and contain none of it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

# Env var to point at an alternate config (used to prove portability by
# running the same code against a different fake config).
CONFIG_PATH_ENV = "DSPACE_DASHBOARD_CONFIG"
DEFAULT_CONFIG_PATH = "config.yaml"
_DEFAULT_USER_AGENT = "dspace-reporting-dashboard/0.1 (+https://github.com/)"


class ConfigError(RuntimeError):
    """Raised when the config file is missing, unparseable, or incomplete."""


@dataclass(frozen=True)
class Community:
    """One top-level community to query, tagged with a department label."""

    label: str
    uuid: str


@dataclass(frozen=True)
class Config:
    base_url: str
    communities: tuple[Community, ...]
    type_map: dict[str, str]
    type_map_default: str = "Other"
    pii_fields: tuple[str, ...] = ("dc.description.provenance",)
    page_size: int = 100
    request_timeout: float = 30.0
    max_retries: int = 3
    user_agent: str = _DEFAULT_USER_AGENT

    def community_pairs(self) -> list[tuple[str, str]]:
        """``(label, uuid)`` tuples in the shape ``DSpaceClient.fetch_all`` wants."""
        return [(c.label, c.uuid) for c in self.communities]


def _resolve_path(path: str | os.PathLike[str] | None) -> Path:
    if path is not None:
        return Path(path)
    return Path(os.environ.get(CONFIG_PATH_ENV, DEFAULT_CONFIG_PATH))


def load_config(path: str | os.PathLike[str] | None = None) -> Config:
    """Read and validate a config file.

    Resolution order: explicit ``path`` arg, then ``$DSPACE_DASHBOARD_CONFIG``,
    then ``./config.yaml``.
    """
    cfg_path = _resolve_path(path)
    if not cfg_path.is_file():
        raise ConfigError(
            f"config file not found: {cfg_path} "
            f"(copy config.example.yaml to config.yaml, or set ${CONFIG_PATH_ENV})"
        )
    try:
        raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"could not parse {cfg_path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{cfg_path}: top level must be a mapping")

    base_url = raw.get("base_url")
    if not isinstance(base_url, str) or not base_url.strip():
        raise ConfigError(f"{cfg_path}: 'base_url' is required")

    communities = _parse_communities(raw.get("communities"), cfg_path)

    type_map_raw = raw.get("type_map", {})
    if not isinstance(type_map_raw, dict) or not type_map_raw:
        raise ConfigError(f"{cfg_path}: 'type_map' must be a non-empty mapping")
    type_map = {str(k): str(v) for k, v in type_map_raw.items()}

    pii_fields = raw.get("pii_fields", ["dc.description.provenance"])
    if not isinstance(pii_fields, list) or not all(
        isinstance(x, str) for x in pii_fields
    ):
        raise ConfigError(f"{cfg_path}: 'pii_fields' must be a list of strings")

    return Config(
        base_url=base_url.strip().rstrip("/"),
        communities=communities,
        type_map=type_map,
        type_map_default=str(raw.get("type_map_default", "Other")),
        pii_fields=tuple(pii_fields),
        page_size=_as_int(raw, "page_size", 100, cfg_path),
        request_timeout=_as_float(raw, "request_timeout", 30.0, cfg_path),
        max_retries=_as_int(raw, "max_retries", 3, cfg_path),
        user_agent=str(raw.get("user_agent", _DEFAULT_USER_AGENT)),
    )


def _parse_communities(value: object, cfg_path: Path) -> tuple[Community, ...]:
    if not isinstance(value, list) or not value:
        raise ConfigError(
            f"{cfg_path}: 'communities' must be a non-empty list of "
            f"{{label, uuid}} entries"
        )
    out: list[Community] = []
    for i, entry in enumerate(value):
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("label"), str)
            or not isinstance(entry.get("uuid"), str)
            or not entry["label"].strip()
            or not entry["uuid"].strip()
        ):
            raise ConfigError(
                f"{cfg_path}: communities[{i}] needs non-empty 'label' and 'uuid'"
            )
        out.append(Community(label=entry["label"].strip(), uuid=entry["uuid"].strip()))
    return tuple(out)


def _as_int(raw: dict, key: str, default: int, cfg_path: Path) -> int:
    val = raw.get(key, default)
    try:
        return int(val)
    except (TypeError, ValueError):
        raise ConfigError(f"{cfg_path}: '{key}' must be an integer") from None


def _as_float(raw: dict, key: str, default: float, cfg_path: Path) -> float:
    val = raw.get(key, default)
    try:
        return float(val)
    except (TypeError, ValueError):
        raise ConfigError(f"{cfg_path}: '{key}' must be a number") from None
