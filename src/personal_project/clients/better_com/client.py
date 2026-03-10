"""Synchronous HTTP client for the Better.com activity booking API.

Uses the ``requests`` library to authenticate against the Better admin portal
and retrieve court availability for a given venue, activity, and date.

Discovered endpoints (from HAR captures):

- ``POST /api/auth/login`` — authenticates with username/password,
  returns a ``token`` in the JSON response body.
- ``GET /api/auth/user`` — verifies the token is still valid; returns user
  info when authenticated.
- ``GET /api/activities/venue/{venue}/activity/{activity}/times?date=YYYY-MM-DD``
  — returns a paginated availability response whose ``data`` array contains
  one element per bookable time slot.

If Better.com changes its API, update the path constants and the parsing logic
in :meth:`BetterClient.get_availability`.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, cast

import requests
from requests import Session

from .credentials import KeyringCredentialHelper

logger = logging.getLogger(__name__)

_LOGIN_PATH: str = "/api/auth/customer/login"
_USER_PATH: str = "/api/auth/user"
_TIMES_PATH: str = "/api/activities/venue/{venue}/activity/{activity}/times"
_HTTP_OK: int = 200
_HTTP_CREATED: int = 201


class BetterClient:
    """Synchronous Better.com client using endpoints discovered in HAR captures.

    Manages authentication via a Bearer token obtained from the customer login
    endpoint and provides a single public method for querying slot availability.

    Usage::

        client = BetterClient()
        ok = client.ensure_logged_in()
        if ok:
            slots = client.get_availability(
                "islington-tennis-centre",
                "tennis-court-indoor",
                date(2026, 3, 11),
            )

    The client is stateful: once logged in, the session retains the
    ``Authorization`` header for all subsequent requests.
    """

    def __init__(
        self,
        base_url: str = "https://better-admin.org.uk",
        session: Session | None = None,
    ) -> None:
        """Initialise the client.

        Args:
            base_url: Root URL of the Better admin portal.  Trailing slashes
                are stripped automatically.
            session: Optional pre-built :class:`requests.Session` to use.
                When ``None`` a new session is created with sensible default
                headers.  Pass an explicit session to inject test doubles.

        """
        self.base_url = base_url.rstrip("/")
        self._session = session or requests.Session()
        self._session.headers.update({
            "User-Agent": "Mozilla/5.0 (compatible; BetterClient/0.1)",
            "Accept": "application/json",
            "Origin": "https://bookings.better.org.uk",
            "Referer": "https://bookings.better.org.uk/",
        })
        self._token: str | None = None

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _get(self, path: str, timeout: int = 10) -> requests.Response:
        """Send a GET request to *path* relative to :attr:`base_url`.

        Args:
            path: URL path to request, e.g. ``"/api/auth/user"``.
            timeout: Request timeout in seconds.

        Returns:
            The :class:`requests.Response` object.

        """
        url = f"{self.base_url}{path}"
        logger.debug("GET %s", url)
        return self._session.get(url, timeout=timeout)

    def _post(
        self,
        path: str,
        *,
        json: dict[str, str | int] | None = None,
        timeout: int = 10,
    ) -> requests.Response:
        """Send a POST request to *path* relative to :attr:`base_url`.

        Args:
            path: URL path to request.
            json: Optional JSON body to serialise and send.
            timeout: Request timeout in seconds.

        Returns:
            The :class:`requests.Response` object.

        """
        url = f"{self.base_url}{path}"
        logger.debug("POST %s", url)
        return self._session.post(url, json=json, timeout=timeout)

    def _set_token(self, token: str) -> None:
        """Store *token* and apply it as a Bearer authorisation header.

        Args:
            token: The JWT or opaque bearer token returned by the login
                endpoint.

        """
        self._token = token
        self._session.headers.update({"Authorization": f"Bearer {token}"})

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def detect_endpoints(self) -> dict[str, Any]:
        """Probe the home page and return basic connection metadata.

        This is a lightweight discovery helper used during development to
        confirm network connectivity before attempting authenticated calls.

        Returns:
            A dict containing at minimum ``{"homepage_status": <http_status>}``.

        """
        resp = self._get("/")
        return {"homepage_status": resp.status_code}

    def login(self, username: str, password: str) -> bool:
        """Authenticate with the customer login endpoint.

        Sends a POST to ``/api/auth/customer/login`` with JSON credentials,
        extracts the ``token`` from the response, stores it, and then verifies
        it by calling :meth:`is_logged_in`.

        Args:
            username: The Better.com account email address.
            password: The account password.

        Returns:
            ``True`` if login succeeded and the token was verified, ``False``
            otherwise.

        """
        try:
            resp = self._post(
                _LOGIN_PATH,
                json={"username": username, "password": password},
            )
            if resp.status_code not in (_HTTP_OK, _HTTP_CREATED):
                logger.debug("login failed status=%s body=%s", resp.status_code, resp.text)
                return False
            data: dict[str, Any] = resp.json()
            token: str | None = data.get("token")
            if not token:
                logger.debug("login response did not contain token: %s", data)
                return False
            self._set_token(token)
            return self.is_logged_in()
        except Exception:
            logger.exception("Error during login")
            return False

    def is_logged_in(self) -> bool:
        """Verify the current session by calling the user-info endpoint.

        Returns:
            ``True`` when the server returns HTTP 200, ``False`` otherwise
            (including on network errors).

        """
        try:
            resp = self._get(_USER_PATH)
        except Exception:
            logger.exception("is_logged_in check failed")
            return False
        else:
            return resp.status_code == _HTTP_OK

    def ensure_logged_in(
        self,
        username: str | None = None,
        password: str | None = None,
        *,
        store: bool = False,
    ) -> bool:
        """Ensure the client holds a valid session token.

        Attempts to authenticate in the following order:

        1. If already logged in, return ``True`` immediately.
        2. If both *username* and *password* are provided, use them.
        3. If only *username* is provided, look up the password from the OS
           keyring via :class:`~.credentials.KeyringCredentialHelper`.
        4. If neither is provided, read both from the keyring or the
           ``BETTER_USERNAME`` / ``BETTER_PASSWORD`` environment variables.

        Args:
            username: Optional account email.  When ``None`` the credential
                helper is used to discover the username.
            password: Optional account password.  When ``None`` the credential
                helper is used after *username* is resolved.
            store: When ``True``, save the supplied credentials to the OS
                keyring on successful login.

        Returns:
            ``True`` if a valid session is established, ``False`` otherwise.

        """
        if self.is_logged_in():
            return True

        if username and password is not None:
            ok = self.login(username, password)
            if ok and store:
                try:
                    KeyringCredentialHelper.set_credentials(username, password)
                except Exception:
                    logger.exception("Failed to store credentials to keyring")
            return ok

        creds = KeyringCredentialHelper.get_credentials(username)
        if creds:
            user, pw = creds
            return self.login(user, pw)

        return False

    def save_credentials(self, username: str, password: str) -> None:
        """Persist *username* and *password* in the OS keyring.

        Args:
            username: The Better.com account email to store.
            password: The corresponding password.

        """
        KeyringCredentialHelper.set_credentials(username, password)

    def delete_credentials(self, username: str) -> None:
        """Remove the stored credentials for *username* from the OS keyring.

        Args:
            username: The account email whose credentials should be deleted.

        """
        KeyringCredentialHelper.delete_credentials(username)

    def get_availability(
        self,
        venue_slug: str,
        activity_slug: str,
        date_obj: date | str,
    ) -> list[dict[str, Any]]:
        """Fetch normalised availability for a venue/activity/date combination.

        Calls ``GET /api/activities/venue/{venue}/activity/{activity}/times``
        and normalises each slot entry into a flat dictionary with consistent
        keys regardless of which time format the API returns.

        Args:
            venue_slug: The venue URL slug, e.g. ``"islington-tennis-centre"``.
            activity_slug: The activity URL slug, e.g.
                ``"tennis-court-indoor"``.
            date_obj: The date to query, either as a
                :class:`datetime.date` or an ISO-format string
                (``"YYYY-MM-DD"``).

        Returns:
            A list of slot dictionaries, each containing the keys:
            ``date``, ``start_iso``, ``end_iso``, ``court_name``,
            ``court_id``, ``is_available``, ``price``, and
            ``source_endpoint``.  Returns an empty list on HTTP errors or
            unexpected response shapes.

        """
        date_str = date_obj.isoformat() if isinstance(date_obj, date) else str(date_obj)
        path = (
            _TIMES_PATH.format(venue=venue_slug, activity=activity_slug)
            + f"?date={date_str}"
        )
        try:
            r = self._get(path)
            if r.status_code != _HTTP_OK:
                logger.debug("availability endpoint returned %s", r.status_code)
                return []
            payload_raw: Any = r.json()
            payload: dict[str, Any] = cast("dict[str, Any]", payload_raw) if isinstance(payload_raw, dict) else {}
            raw_data: Any = payload.get("data")
            data: list[Any] = cast("list[Any]", raw_data) if isinstance(raw_data, list) else []
            out: list[dict[str, Any]] = []
            for item in data:
                starts: dict[str, Any] = item.get("starts_at") or {}
                ends: dict[str, Any] = item.get("ends_at") or {}
                start_24: str | None = starts.get("format_24_hour") or starts.get("format_12_hour")
                end_24: str | None = ends.get("format_24_hour") or ends.get("format_12_hour")
                item_date: str = item.get("date") or date_str
                start_iso = f"{item_date}T{start_24}:00" if start_24 and ":" in start_24 else None
                end_iso = f"{item_date}T{end_24}:00" if end_24 and ":" in end_24 else None
                action: dict[str, Any] = item.get("action_to_show") or {}
                status: str | None = action.get("status")
                booking: object = item.get("booking")
                is_available: bool = (status == "BOOK") and (booking in (None, "", []))
                price_obj: dict[str, Any] = item.get("price") or {}
                price_formatted: str | None = price_obj.get("formatted_amount")
                out.append({
                    "date": item_date,
                    "start_iso": start_iso,
                    "end_iso": end_iso,
                    "court_name": item.get("name"),
                    "court_id": item.get("composite_key"),
                    "is_available": is_available,
                    "price": price_formatted,
                    "source_endpoint": f"{self.base_url}{path}",
                })
        except Exception:
            logger.exception("Error fetching availability")
            return []
        else:
            return out

    def close(self) -> None:
        """Close the underlying HTTP session and release connections."""
        self._session.close()
