"""Utilities to discover API endpoints from Playwright HAR archive files.

Provides two public functions:

- :func:`discover_from_har` — parses a ``.har`` JSON file and returns all
  XHR/fetch-like request entries as structured dictionaries.
- :func:`summarize_har_endpoints` — convenience wrapper that returns a compact
  summary list suitable for manual inspection.

Both functions are intentionally defensive: they tolerate missing or
unexpectedly-typed fields in HAR files produced by different tools (Playwright,
Chrome DevTools, Burp Suite, etc.).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, cast

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Private helpers for safe JSON traversal under pyright strict mode
# ---------------------------------------------------------------------------


def _d(obj: Any) -> dict[str, Any]:
    """Return *obj* as a ``dict[str, Any]`` if it is a mapping, else ``{}``.

    Args:
        obj: Any value, typically from a ``json.loads`` result.

    Returns:
        The object cast to ``dict[str, Any]`` when it is a :class:`dict`,
        or an empty dict otherwise.

    """
    return cast("dict[str, Any]", obj) if isinstance(obj, dict) else {}


def _l(obj: Any) -> list[Any]:
    """Return *obj* as a ``list[Any]`` if it is a sequence, else ``[]``.

    Args:
        obj: Any value, typically from a ``json.loads`` result.

    Returns:
        The object cast to ``list[Any]`` when it is a :class:`list`,
        or an empty list otherwise.

    """
    return cast("list[Any]", obj) if isinstance(obj, list) else []


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def discover_from_har(har_path: str | Path) -> dict[str, Any]:
    """Parse a HAR file and return structured info about XHR/fetch entries.

    Reads the JSON-formatted HAR archive at *har_path*, filters entries to
    those that look like API (XHR/fetch) calls, and returns them in a
    normalised format.

    An entry is classified as an API call when any of the following is true:

    - The ``X-Requested-With: XMLHttpRequest`` request header is present.
    - The request method is ``POST``, ``PUT``, or ``PATCH`` and has a body.
    - The request or response content type contains ``application/json``.
    - The HAR ``_resourceType`` or ``_initiatorType`` field is ``"xhr"``,
      ``"fetch"``, or ``"api"``.

    Args:
        har_path: Path to the ``.har`` file.  Accepts both :class:`str` and
            :class:`~pathlib.Path`.

    Returns:
        A dict with a single key ``"entries"``, whose value is a list of
        dicts each containing: ``method``, ``url``, ``status``,
        ``req_headers``, ``req_post_data``, ``resp_headers``, ``resp_text``.

    Raises:
        FileNotFoundError: If *har_path* does not exist.

    """
    p = Path(har_path)
    if not p.exists():
        msg = f"HAR not found: {har_path}"
        raise FileNotFoundError(msg)

    raw: Any = json.loads(p.read_text(encoding="utf-8"))
    har = _d(raw)
    log = _d(har.get("log") or har)
    entries = _l(log.get("entries") or [])

    results: list[dict[str, Any]] = []

    for e in entries:
        try:
            entry = _d(e)
            req = _d(entry.get("request") or {})
            resp = _d(entry.get("response") or {})
            content = _d(resp.get("content") or {})
            mime: str = content.get("mimeType") or ""

            hdr_list = _l(req.get("headers") or [])
            headers: dict[str, str] = {
                str(_d(h).get("name") or ""): str(_d(h).get("value") or "")
                for h in hdr_list
                if isinstance(h, dict)
            }

            resource_type: str = str(entry.get("_resourceType") or entry.get("_initiatorType") or "")
            method: str = str(req.get("method") or "GET").upper()

            is_xhr = (
                headers.get("x-requested-with", "").lower() == "xmlhttprequest"
                or (method in ("POST", "PUT", "PATCH") and req.get("postData"))
                or "application/json" in (
                    headers.get("accept", "")
                    + headers.get("content-type", "")
                    + mime
                )
                or resource_type.lower() in ("xhr", "fetch", "api")
            )

            if not is_xhr:
                continue

            post_data = _d(req.get("postData") or {})
            request_body: str | None = post_data.get("text")

            resp_content = _d(resp.get("content") or {})
            resp_text: str | None = resp_content.get("text")

            resp_hdr_list = _l(resp.get("headers") or [])
            resp_headers: dict[str, str] = {
                str(_d(h).get("name") or ""): str(_d(h).get("value") or "")
                for h in resp_hdr_list
                if isinstance(h, dict)
            }

            results.append({
                "method": req.get("method"),
                "url": req.get("url"),
                "status": resp.get("status"),
                "req_headers": headers,
                "req_post_data": request_body,
                "resp_headers": resp_headers,
                "resp_text": resp_text,
            })
        except Exception:
            logger.exception("Failed to parse HAR entry")

    return {"entries": results}


def summarize_har_endpoints(har_path: str | Path) -> list[dict[str, Any]]:
    """Return a compact summary of API endpoints found in a HAR file.

    Calls :func:`discover_from_har` and reduces each entry to the fields most
    useful for manual endpoint inspection: method, URL, status, a sample of
    the request body, and the first 1 000 characters of the response body.

    Args:
        har_path: Path to the ``.har`` file.

    Returns:
        A list of dicts each containing: ``method``, ``url``, ``status``,
        ``sample_request``, ``sample_response``.

    Raises:
        FileNotFoundError: If *har_path* does not exist.

    """
    data = discover_from_har(har_path)
    out: list[dict[str, Any]] = []
    for e in _l(data.get("entries") or []):
        entry = _d(e)
        resp_text: Any = entry.get("resp_text")
        sample_response: str | None = resp_text[:1000] if isinstance(resp_text, str) else None
        out.append({
            "method": entry.get("method"),
            "url": entry.get("url"),
            "status": entry.get("status"),
            "sample_request": entry.get("req_post_data"),
            "sample_response": sample_response,
        })
    return out
