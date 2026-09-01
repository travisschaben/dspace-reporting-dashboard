"""Thin client for the DSpace 7+ REST discovery API.

Responsibilities: pagination and community-scoped item queries. Nothing in this
module is institution-specific and nothing here normalizes data -- raw DSpace
item objects come out exactly as the API returned them.

The discovery API is public by default, so no authentication is performed. An
institution running a non-public API would need to add auth handling here.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Any

import requests

# Endpoint path is stable across DSpace 7 through 10.
_SEARCH_PATH = "/api/discover/search/objects"

# HTTP statuses worth a retry: rate limiting and transient server errors.
_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


class DSpaceAPIError(RuntimeError):
    """Raised when the discovery API cannot be reached or returns an error."""


def _dig(obj: Any, *keys: str, default: Any = None) -> Any:
    """Walk a chain of dict keys, returning ``default`` if any link is missing.

    DSpace responses nest results several ``_embedded`` levels deep and any
    level can be absent (empty scope, changed API shape); never assume a key
    or list index exists.
    """
    cur = obj
    for key in keys:
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return cur


class DSpaceClient:
    """Paginating client bound to a single DSpace server base URL.

    Parameters
    ----------
    base_url:
        Server root, e.g. ``https://dspace.example.edu/server``. A trailing
        slash is fine; ``/api/...`` paths are appended to it.
    page_size:
        Discovery page size. DSpace caps this (commonly at 100).
    timeout:
        Per-request timeout in seconds.
    user_agent:
        Sent as the ``User-Agent`` header so instance operators can see who
        is crawling.
    max_retries:
        Attempts per request on a retryable status or connection error,
        beyond the first try.
    session:
        Optional pre-built ``requests.Session`` (mainly for tests).
    """

    def __init__(
        self,
        base_url: str,
        *,
        page_size: int = 100,
        timeout: float = 30.0,
        user_agent: str = "dspace-reporting-dashboard/0.1 (+https://github.com/)",
        max_retries: int = 3,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.page_size = page_size
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = session or requests.Session()
        self.session.headers.update(
            {"Accept": "application/json", "User-Agent": user_agent}
        )

    # -- low-level -----------------------------------------------------------

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
            except requests.RequestException as exc:  # connection/DNS/timeout
                last_exc = exc
            else:
                if resp.status_code in _RETRY_STATUSES:
                    last_exc = DSpaceAPIError(
                        f"{resp.status_code} from {resp.url}"
                    )
                elif not resp.ok:
                    raise DSpaceAPIError(f"{resp.status_code} from {resp.url}")
                else:
                    try:
                        return resp.json()
                    except ValueError as exc:
                        raise DSpaceAPIError(
                            f"non-JSON response from {resp.url}"
                        ) from exc
            if attempt < self.max_retries:
                time.sleep(2**attempt)  # 1s, 2s, 4s, ...
        raise DSpaceAPIError(
            f"GET {url} failed after {self.max_retries + 1} attempts"
        ) from last_exc

    # -- public -----------------------------------------------------------

    def iter_scope_items(self, scope_uuid: str) -> Iterator[dict[str, Any]]:
        """Yield every raw item object within one community/collection scope.

        Pages are walked until one comes back shorter than ``page_size``; the
        response's ``totalPages``/``totalElements`` counters are ignored on
        purpose -- they have been observed to lag behind reality on large or
        recently-changed scopes.
        """
        page = 0
        while True:
            payload = self._get(
                _SEARCH_PATH,
                {
                    "scope": scope_uuid,
                    "dsoType": "item",
                    "size": self.page_size,
                    "page": page,
                },
            )
            objects = _dig(
                payload,
                "_embedded",
                "searchResult",
                "_embedded",
                "objects",
                default=[],
            )
            for entry in objects:
                item = _dig(entry, "_embedded", "indexableObject")
                if item is not None:
                    yield item
            if len(objects) < self.page_size:
                return
            page += 1

    def fetch_all(
        self, communities: list[tuple[str, str]]
    ) -> list[tuple[str, dict[str, Any]]]:
        """Fetch items for every ``(department_label, community_uuid)`` pair.

        Returns a list of ``(department_label, raw_item)`` tuples. Items are
        de-duplicated by UUID across scopes; the first scope an item appears
        in wins its department label.
        """
        seen: set[str] = set()
        results: list[tuple[str, dict[str, Any]]] = []
        for label, uuid in communities:
            for item in self.iter_scope_items(uuid):
                item_uuid = item.get("uuid")
                if item_uuid is None or item_uuid in seen:
                    continue
                seen.add(item_uuid)
                results.append((label, item))
        return results
