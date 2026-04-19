"""Synchronous HTTP client for the Better.com activity booking API.

Uses the ``requests`` library to authenticate against the Better admin portal,
retrieve court availability, and book court slots for a given venue, activity,
and date.

Discovered endpoints (from HAR captures):

- ``POST /api/auth/customer/login`` — authenticates with username/password,
  returns a ``token`` in the JSON response body.
- ``GET /api/auth/user`` — verifies the token is still valid; returns user
  info including the integer ``id`` needed for cart calls.
- ``GET /api/activities/venue/{venue}/activity/{activity}/times?date=YYYY-MM-DD``
  — returns a paginated availability response whose ``data`` array contains
  one element per bookable time slot.
- ``GET /api/activities/venue/{venue}/activity/{activity}/slots?date=YYYY-MM-DD&...``
  — returns detailed slot data including the ``id`` and ``pricing_option_id``
  needed to add a slot to the cart.
- ``POST /api/activities/cart/add`` — adds a slot to the user's cart.
- ``GET /api/activities/cart`` — retrieves the current cart contents.
- ``POST /api/activities/cart/remove`` — removes items from the cart.
- ``GET /api/checkout/prepare`` — returns payment provider configuration.
- ``POST /api/checkout/authorise`` — submits card tokenisation data to
  begin the payment.
- ``POST /api/checkout/complete`` — finalises the booking.

If Better.com changes its API, update the path constants and the parsing logic
in the relevant methods.
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
_SLOTS_PATH: str = "/api/activities/venue/{venue}/activity/{activity}/slots"
_CART_ADD_PATH: str = "/api/activities/cart/add"
_CART_GET_PATH: str = "/api/activities/cart"
_CART_REMOVE_PATH: str = "/api/activities/cart/remove"
_CHECKOUT_PREPARE_PATH: str = "/api/checkout/prepare"
_CHECKOUT_AUTHORISE_PATH: str = "/api/checkout/authorise"
_CHECKOUT_COMPLETE_PATH: str = "/api/checkout/complete"
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
        json: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
        timeout: int = 10,
    ) -> requests.Response:
        """Send a POST request to *path* relative to :attr:`base_url`.

        Args:
            path: URL path to request.
            json: Optional JSON body to serialise and send.
            params: Optional query-string parameters.
            timeout: Request timeout in seconds.

        Returns:
            The :class:`requests.Response` object.

        """
        url = f"{self.base_url}{path}"
        logger.debug("POST %s", url)
        return self._session.post(url, json=json, params=params, timeout=timeout)

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

    def get_user_id(self) -> int | None:
        """Return the authenticated user's integer ID from the user-info endpoint.

        Returns:
            The user's integer ``id`` on success, or ``None`` if the request
            fails or the user is not authenticated.

        """
        try:
            resp = self._get(_USER_PATH)
            if resp.status_code != _HTTP_OK:
                return None
            payload: Any = resp.json()
            payload_d: dict[str, Any] = cast("dict[str, Any]", payload) if isinstance(payload, dict) else {}
            data: Any = payload_d.get("data")
            if isinstance(data, dict):
                data_d: dict[str, Any] = cast("dict[str, Any]", data)
                user_id: Any = data_d.get("id")
                return int(user_id) if isinstance(user_id, int) else None
        except Exception:
            logger.exception("Error fetching user ID")
        return None

    def get_slot_details(
        self,
        venue_slug: str,
        activity_slug: str,
        date_obj: date | str,
        start_time: str,
        end_time: str,
        composite_key: str,
    ) -> dict[str, Any] | None:
        """Fetch detailed slot data needed to add a specific slot to the cart.

        Calls ``GET /api/activities/venue/{venue}/activity/{activity}/slots``
        with the date, start/end times, and composite key that identify the
        slot.  The response contains the ``id`` and ``pricing_option_id``
        required by :meth:`add_to_cart`.

        Args:
            venue_slug: The venue URL slug, e.g. ``"islington-tennis-centre"``.
            activity_slug: The activity URL slug, e.g. ``"tennis-court-indoor"``.
            date_obj: The slot date as a :class:`datetime.date` or ISO string.
            start_time: Slot start in ``HH:MM`` format, e.g. ``"18:00"``.
            end_time: Slot end in ``HH:MM`` format, e.g. ``"19:00"``.
            composite_key: The ``court_id`` / ``composite_key`` from the
                availability response.

        Returns:
            A dict containing at minimum ``id``, ``pricing_option_id``, and
            ``cart_type``, or ``None`` if the slot could not be retrieved.

        """
        date_str = date_obj.isoformat() if isinstance(date_obj, date) else str(date_obj)
        path = (
            _SLOTS_PATH.format(venue=venue_slug, activity=activity_slug)
            + f"?date={date_str}&start_time={start_time}&end_time={end_time}"
            + f"&composite_key={composite_key}"
        )
        try:
            resp = self._get(path)
            if resp.status_code != _HTTP_OK:
                logger.debug("get_slot_details returned %s", resp.status_code)
                return None
            payload: Any = resp.json()
            payload_d: dict[str, Any] = cast("dict[str, Any]", payload) if isinstance(payload, dict) else {}
            data: Any = payload_d.get("data")
            items: list[Any] = cast("list[Any]", data) if isinstance(data, list) else []
            return cast("dict[str, Any]", items[0]) if items else None
        except Exception:
            logger.exception("Error fetching slot details")
            return None

    def add_to_cart(
        self,
        slot_id: int,
        pricing_option_id: int,
        user_id: int,
    ) -> dict[str, Any] | None:
        """Add a court slot to the user's cart.

        Calls ``POST /api/activities/cart/add`` with the slot, pricing option,
        and user identifiers.  On success the response body contains the cart
        item including its ``id`` and ``itemHash`` needed for checkout.

        Args:
            slot_id: The integer ``id`` of the slot (from :meth:`get_slot_details`).
            pricing_option_id: The pricing option ID for the slot.
            user_id: The authenticated user's integer ID (from :meth:`get_user_id`).

        Returns:
            The cart response dict on success, or ``None`` on failure.

        """
        payload: dict[str, Any] = {
            "items": [
                {
                    "id": slot_id,
                    "type": "activity",
                    "purchased_for_user_id": None,
                    "pricing_option_id": pricing_option_id,
                }
            ],
            "membership_user_id": None,
            "selected_user_id": user_id,
        }
        try:
            resp = self._post(_CART_ADD_PATH, json=payload)
            if resp.status_code not in (_HTTP_OK, _HTTP_CREATED):
                logger.debug("add_to_cart returned %s: %s", resp.status_code, resp.text)
                return None
            result: Any = resp.json()
            return cast("dict[str, Any]", result) if isinstance(result, dict) else None
        except Exception:
            logger.exception("Error adding to cart")
            return None

    def get_cart(self, user_id: int) -> dict[str, Any] | None:
        """Retrieve the current cart contents for the authenticated user.

        Args:
            user_id: The authenticated user's integer ID.

        Returns:
            The cart response dict, or ``None`` on failure.

        """
        path = f"{_CART_GET_PATH}?selected_user_id={user_id}"
        try:
            resp = self._get(path)
            if resp.status_code != _HTTP_OK:
                return None
            result: Any = resp.json()
            return cast("dict[str, Any]", result) if isinstance(result, dict) else None
        except Exception:
            logger.exception("Error fetching cart")
            return None

    def remove_from_cart(self, cart_item_ids: list[int], user_id: int) -> bool:
        """Remove one or more items from the user's cart.

        Args:
            cart_item_ids: List of cart item IDs to remove.
            user_id: The authenticated user's integer ID.

        Returns:
            ``True`` on success, ``False`` otherwise.

        """
        try:
            resp = self._post(
                _CART_REMOVE_PATH,
                json={"cart_item_ids": cart_item_ids},
                params={"selected_user_id": str(user_id)},
            )
        except Exception:
            logger.exception("Error removing from cart")
            return False
        else:
            return resp.status_code in (_HTTP_OK, _HTTP_CREATED)

    def tokenise_card_opayo(
        self,
        opayo_url: str,
        session_key: str,
        card_number: str,
        expiry_mmyy: str,
        security_code: str,
        cardholder_name: str,
    ) -> str | None:
        """Submit card details to Opayo and return a one-time ``cardIdentifier``.

        Calls ``POST {opayo_url}/api/v1/card-identifiers`` with the raw card
        data.  The returned ``cardIdentifier`` is a short-lived token that
        replaces the actual card number in the authorise call, ensuring raw
        PAN data never reaches the Better.com servers.

        Args:
            opayo_url: The Opayo base URL from :meth:`checkout_prepare`, e.g.
                ``"https://live.opayo.eu.elavon.com"``.
            session_key: The merchant session key from :meth:`checkout_prepare`,
                used as a Bearer token to authenticate with Opayo.
            card_number: The 16-digit card number (no spaces).
            expiry_mmyy: Card expiry in ``MMYY`` format, e.g. ``"1225"``.
            security_code: The 3- or 4-digit CVV / security code.
            cardholder_name: Name as printed on the card.

        Returns:
            A ``cardIdentifier`` UUID string on success, or ``None`` on failure.

        """
        url = f"{opayo_url}/api/v1/card-identifiers"
        try:
            resp = self._session.post(
                url,
                headers={"Authorization": f"Bearer {session_key}", "Content-Type": "application/json"},
                json={
                    "cardDetails": {
                        "cardholderName": cardholder_name,
                        "cardNumber": card_number,
                        "expiryDate": expiry_mmyy,
                        "securityCode": security_code,
                    }
                },
                timeout=15,
            )
            if resp.status_code not in (_HTTP_OK, _HTTP_CREATED):
                logger.debug("Opayo tokenise returned %s: %s", resp.status_code, resp.text)
                return None
            result: Any = resp.json()
            result_d: dict[str, Any] = cast("dict[str, Any]", result) if isinstance(result, dict) else {}
            identifier: Any = result_d.get("cardIdentifier")
            return str(identifier) if isinstance(identifier, str) else None
        except Exception:
            logger.exception("Error tokenising card with Opayo")
            return None

    def checkout_prepare(self) -> dict[str, Any] | None:
        """Fetch payment provider configuration for the checkout flow.

        Calls ``GET /api/checkout/prepare`` to retrieve the Stripe publishable
        key, client secret, or SagePay session key needed to tokenise card
        details on the client side before authorising the payment.

        Returns:
            The prepare response dict, or ``None`` on failure.

        """
        try:
            resp = self._get(_CHECKOUT_PREPARE_PATH)
            if resp.status_code != _HTTP_OK:
                logger.debug("checkout_prepare returned %s: %s", resp.status_code, resp.text)
                return None
            result: Any = resp.json()
            return cast("dict[str, Any]", result) if isinstance(result, dict) else None
        except Exception:
            logger.exception("Error calling checkout/prepare")
            return None

    def checkout_authorise(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        """Submit card tokenisation data to begin the payment authorisation.

        Calls ``POST /api/checkout/authorise`` with billing address and card
        token details obtained from the payment provider (e.g. SagePay or
        Stripe).  The exact payload shape depends on the provider returned by
        :meth:`checkout_prepare`.

        Args:
            payload: The authorisation payload, which must include at minimum
                ``billingFirstName``, ``billingLastName``, ``billingPostcode``,
                ``cardIdentifier``, ``sessionKey``, and ``cardholderName``.

        Returns:
            The authorise response dict on success, or ``None`` on failure.

        """
        try:
            resp = self._post(_CHECKOUT_AUTHORISE_PATH, json=payload)
            if resp.status_code not in (_HTTP_OK, _HTTP_CREATED):
                logger.debug("checkout_authorise returned %s: %s", resp.status_code, resp.text)
                return None
            result: Any = resp.json()
            return cast("dict[str, Any]", result) if isinstance(result, dict) else None
        except Exception:
            logger.exception("Error calling checkout/authorise")
            return None

    def checkout_complete(
        self,
        item_hash: str,
        user_id: int,
        payments: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Finalise the booking after payment authorisation.

        Calls ``POST /api/checkout/complete`` with the cart item hash, user
        ID, and payment reference data returned by :meth:`checkout_authorise`.

        Args:
            item_hash: The ``itemHash`` string from the cart item, identifying
                the items being purchased.
            user_id: The authenticated user's integer ID.
            payments: List of payment reference dicts from the authorise step.

        Returns:
            The checkout-complete response dict on success, or ``None`` on
            failure.

        """
        payload: dict[str, Any] = {
            "completed_waivers": [],
            "payments": payments,
            "selected_user_id": user_id,
            "source": "activity-booking",
            "terms": True,
            "item_hash": item_hash,
        }
        try:
            resp = self._post(_CHECKOUT_COMPLETE_PATH, json=payload)
            if resp.status_code not in (_HTTP_OK, _HTTP_CREATED):
                logger.debug("checkout_complete returned %s: %s", resp.status_code, resp.text)
                return None
            result: Any = resp.json()
            return cast("dict[str, Any]", result) if isinstance(result, dict) else None
        except Exception:
            logger.exception("Error calling checkout/complete")
            return None

    def close(self) -> None:
        """Close the underlying HTTP session and release connections."""
        self._session.close()
