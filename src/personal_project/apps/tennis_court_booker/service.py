"""Service layer for the tennis court booker application.

Provides the primary public functions of the tennis court booker:

* :func:`get_venue_availability` — fetch and return a full availability
  summary for a ClubSpark venue on a given date, optionally filtered to a
  specific set of start-time hours.
* :func:`get_venue_availability_better` — fetch and return an availability
  summary for a Better.com venue and activity, concatenating across multiple
  activities when more than one is provided.
* :func:`check_slot_availability` — confirm whether a specific ClubSpark
  court and time slot is still bookable.

Both ClubSpark functions delegate browser automation to
:class:`~personal_project.clients.clubspark.ClubSparkClient`.  The Better.com
functions delegate to :class:`~personal_project.clients.better_com.BetterClient`,
which is synchronous; calls are dispatched via :func:`asyncio.to_thread` to
avoid blocking the event loop.
"""

from __future__ import annotations

import asyncio
import datetime
from typing import Any

from personal_project.apps.tennis_court_booker.models import (
    BookingResult,
    CardDetails,
    CourtSlot,
    VenueAvailability,
)
from personal_project.clients.better_com.client import BetterClient
from personal_project.clients.clubspark.client import ClubSparkClient, RawSlot


def _raw_to_court_slot(raw: RawSlot, date: datetime.date) -> CourtSlot:
    """Convert a :class:`RawSlot` from the ClubSpark client into a :class:`CourtSlot`.

    Args:
        raw: The raw slot data returned by the ClubSpark client.
        date: The date on which the slot falls.

    Returns:
        A fully-populated :class:`CourtSlot` domain object.

    """
    return CourtSlot(
        court_name=raw.court_name,
        date=date,
        start_time=raw.start_time,
        end_time=raw.end_time,
        is_available=raw.is_available,
        price=raw.price,
    )


def _better_raw_to_court_slot(raw: dict[str, Any], date: datetime.date) -> CourtSlot | None:
    """Convert a Better.com raw slot dict into a :class:`CourtSlot`.

    Parses the ISO-formatted ``start_iso`` and ``end_iso`` strings from the
    Better.com API response and maps the remaining fields to the domain model.

    Args:
        raw: A slot dictionary as returned by
            :meth:`~personal_project.clients.better_com.BetterClient.get_availability`.
        date: The date on which the slot falls.

    Returns:
        A :class:`CourtSlot` on success, or ``None`` when required fields
        (``start_iso``, ``end_iso``, ``court_name``) are absent or
        unparseable.

    """
    start_iso = raw.get("start_iso")
    end_iso = raw.get("end_iso")
    if not isinstance(start_iso, str) or not isinstance(end_iso, str):
        return None

    try:
        start_time = datetime.time.fromisoformat(start_iso.split("T")[1][:5])
        end_time = datetime.time.fromisoformat(end_iso.split("T")[1][:5])
    except (ValueError, IndexError):
        return None

    court_name_raw = raw.get("court_name")
    if not isinstance(court_name_raw, str) or not court_name_raw:
        return None

    price_raw = raw.get("price")
    price: str | None = str(price_raw) if isinstance(price_raw, str) else None

    return CourtSlot(
        court_name=court_name_raw,
        date=date,
        start_time=start_time,
        end_time=end_time,
        is_available=bool(raw.get("is_available")),
        price=price,
    )


async def get_venue_availability(
    venue: str,
    date: datetime.date,
    *,
    hours: list[int] | None = None,
    client: ClubSparkClient | None = None,
) -> VenueAvailability:
    """Retrieve a court-availability summary for a ClubSpark venue on a given date.

    Navigate to the ClubSpark booking page for *venue* as a guest, scrape all
    court slots from the rendered booking grid, optionally filter to slots
    starting at the specified *hours*, and return the result as a
    :class:`VenueAvailability` object.

    Args:
        venue: ClubSpark venue slug as it appears in the booking URL, e.g.
            ``"BurgessParkSouthwark"``.
        date: The date for which to retrieve availability.
        hours: Optional list of start-time hours (0-23) to include.  When
            provided, only slots whose ``start_time.hour`` is in *hours* are
            returned.  When ``None``, all slots are returned unfiltered.
        client: Optional pre-constructed
            :class:`~personal_project.clients.clubspark.ClubSparkClient`
            instance.  When ``None`` a default client is created automatically.

    Returns:
        A :class:`VenueAvailability` containing the matched slots (both
        available and unavailable).

    Raises:
        TimeoutError: If the booking grid does not load within the client
            timeout.
        ValueError: If the page structure cannot be interpreted.

    """
    effective_client = client or ClubSparkClient()
    raw_slots = await effective_client.get_available_slots(venue, date)
    court_slots = [_raw_to_court_slot(raw, date) for raw in raw_slots]

    if hours is not None:
        hours_set = set(hours)
        court_slots = [s for s in court_slots if s.start_time.hour in hours_set]

    return VenueAvailability(venue=venue, date=date, slots=court_slots)


async def get_venue_availability_better(
    venue: str,
    activity: str,
    date: datetime.date,
    *,
    hours: list[int] | None = None,
    client: BetterClient | None = None,
) -> VenueAvailability:
    """Retrieve a court-availability summary for a Better.com venue and activity.

    Authenticates with the Better.com API (using OS keyring credentials or
    environment variables), fetches all time slots for *venue* / *activity* on
    *date*, converts them to domain objects, and optionally filters by
    start-time hour.

    The underlying :class:`~personal_project.clients.better_com.BetterClient`
    is synchronous; all network calls are dispatched via
    :func:`asyncio.to_thread` so they do not block the event loop.

    Args:
        venue: Better.com venue slug, e.g. ``"islington-tennis-centre"``.
        activity: Better.com activity slug, e.g. ``"tennis-court-indoor"``.
        date: The date for which to retrieve availability.
        hours: Optional list of start-time hours (0-23) to include.  When
            ``None``, all slots are returned.
        client: Optional pre-authenticated
            :class:`~personal_project.clients.better_com.BetterClient`
            instance.  When ``None`` a default client is created and
            :meth:`~personal_project.clients.better_com.BetterClient.ensure_logged_in`
            is called automatically.

    Returns:
        A :class:`VenueAvailability` containing the matched slots.

    Raises:
        RuntimeError: If authentication with Better.com fails.

    """
    effective_client = client or BetterClient()

    ok: bool = await asyncio.to_thread(effective_client.ensure_logged_in)
    if not ok:
        msg = f"BetterClient authentication failed for venue '{venue}'."
        raise RuntimeError(msg)

    raw_slots: list[dict[str, Any]] = await asyncio.to_thread(
        effective_client.get_availability, venue, activity, date
    )

    court_slots = [
        slot
        for raw in raw_slots
        if (slot := _better_raw_to_court_slot(raw, date)) is not None
    ]

    if hours is not None:
        hours_set = set(hours)
        court_slots = [s for s in court_slots if s.start_time.hour in hours_set]

    return VenueAvailability(venue=venue, date=date, slots=court_slots)


async def book_court_better(  # noqa: PLR0911
    venue: str,
    activity: str,
    date: datetime.date,
    start_time: datetime.time,
    composite_key: str,
    card: CardDetails,
    *,
    client: BetterClient | None = None,
) -> BookingResult:
    """Book a specific Better.com court slot using card payment via Opayo.

    Orchestrates the full booking flow:

    1. Authenticate with the Better.com API.
    2. Fetch detailed slot data (``id`` and ``pricing_option_id``).
    3. Add the slot to the user's cart.
    4. Retrieve the Opayo merchant session key from the checkout prepare
       endpoint.
    5. Tokenise the card details with Opayo to obtain a ``cardIdentifier``.
    6. Authorise the payment, including the 3D Secure browser-fingerprint
       fields required by Opayo.
    7. On a successful authorisation, the transaction UUID is returned as the
       booking reference.

    All synchronous network calls are dispatched via :func:`asyncio.to_thread`
    to avoid blocking the event loop.

    Args:
        venue: Better.com venue slug, e.g. ``"islington-tennis-centre"``.
        activity: Better.com activity slug, e.g. ``"tennis-court-indoor"``.
        date: The date of the slot to book.
        start_time: The start time of the slot to book.
        composite_key: The ``court_id`` / ``composite_key`` from the
            availability response that uniquely identifies the slot.
        card: :class:`~models.CardDetails` containing the payment card and
            billing address information.
        client: Optional pre-authenticated :class:`~BetterClient` instance.
            When ``None`` a default client is created and
            :meth:`~BetterClient.ensure_logged_in` is called automatically.

    Returns:
        A :class:`~models.BookingResult` with :attr:`~BookingResult.success`
        set to ``True`` and a transaction reference on success, or
        :attr:`~BookingResult.success` set to ``False`` with an error message
        on failure.

    Raises:
        RuntimeError: If authentication with Better.com fails.

    """
    effective_client = client or BetterClient()

    ok: bool = await asyncio.to_thread(effective_client.ensure_logged_in)
    if not ok:
        raise RuntimeError(f"BetterClient authentication failed for venue '{venue}'.")

    user_id: int | None = await asyncio.to_thread(effective_client.get_user_id)
    if not user_id:
        return BookingResult(success=False, error="Could not retrieve user ID.")

    # Build end time string (assume 1-hour slots)
    end_time = datetime.time(
        (start_time.hour + 1) % 24,
        start_time.minute,
    )
    start_str = start_time.strftime("%H:%M")
    end_str = end_time.strftime("%H:%M")

    slot = await asyncio.to_thread(
        effective_client.get_slot_details,
        venue, activity, date, start_str, end_str, composite_key,
    )
    if not slot:
        return BookingResult(success=False, error=f"Slot not found for {start_str} at {venue}.")

    slot_id: int = int(slot["id"])
    pricing_option_id: int = int(slot["pricing_option_id"])

    cart = await asyncio.to_thread(
        effective_client.add_to_cart, slot_id, pricing_option_id, user_id
    )
    if not cart:
        return BookingResult(success=False, error="Failed to add slot to cart.")

    cart_data: dict[str, Any] = cart.get("data") or {}
    item_hash: str = str(cart_data.get("itemHash", ""))
    cart_items: list[Any] = cart_data.get("items") or []
    cart_item_ids: list[int] = [
        int(i["id"]) for i in cart_items  # type: ignore[index]
        if isinstance(i, dict) and "id" in i
    ]

    prep = await asyncio.to_thread(effective_client.checkout_prepare)
    if not prep:
        await asyncio.to_thread(effective_client.remove_from_cart, cart_item_ids, user_id)
        return BookingResult(success=False, error="Failed to prepare checkout.")

    session_key: str = str(prep.get("session_key", ""))
    opayo_cfg: dict[str, Any] = prep.get("payment_provider_configuration") or {}
    opayo_url: str = str(opayo_cfg.get("url", ""))

    card_identifier = await asyncio.to_thread(
        effective_client.tokenise_card_opayo,
        opayo_url, session_key,
        card.card_number, card.expiry_mmyy, card.security_code, card.cardholder_name,
    )
    if not card_identifier:
        await asyncio.to_thread(effective_client.remove_from_cart, cart_item_ids, user_id)
        return BookingResult(success=False, error="Card tokenisation failed.")

    auth_payload: dict[str, Any] = {
        "session_key": session_key,
        "card_identifier": card_identifier,
        "cardholder_name": card.cardholder_name,
        "billing_first_name": card.billing_first_name,
        "billing_last_name": card.billing_last_name,
        "billing_address_line_one": card.billing_address_line_one,
        "billing_address_line_two": card.billing_address_line_two,
        "billing_city": card.billing_city,
        "billing_postcode": card.billing_postcode,
        "save_card": False,
        "saved_card_id": None,
        "completed_waivers": [],
        "browser_language": "en-GB",
        "browser_screen_width": 1440,
        "browser_screen_height": 900,
        "browser_color_depth": 24,
        "browser_colour_depth": 24,
        "browser_tz": -60,
        "browser_timezone_offset": -60,
        "browser_java_enabled": False,
        "browser_javascript_enabled": True,
        "challenge_window_size": "05",
        "notification_url": "https://bookings.better.org.uk/checkout/3ds-notification",
        "source": "activity-booking",
        "selected_user_id": user_id,
        "item_hash": item_hash,
        "terms": [True],
    }

    auth_resp = await asyncio.to_thread(effective_client.checkout_authorise, auth_payload)

    if not auth_resp:
        await asyncio.to_thread(effective_client.remove_from_cart, cart_item_ids, user_id)
        return BookingResult(success=False, error="Authorisation request failed.")

    tx_status: str = str(auth_resp.get("transaction_status", ""))
    tx_uuid: str = str(auth_resp.get("transaction_uuid", ""))
    error_info: dict[str, Any] = auth_resp.get("error") or {}
    error_msg: str | None = error_info.get("message") if error_info else None
    sca_url: str | None = auth_resp.get("sca_url")

    if sca_url:
        # 3D Secure challenge required — cannot complete headlessly
        await asyncio.to_thread(effective_client.remove_from_cart, cart_item_ids, user_id)
        return BookingResult(
            success=False,
            error=f"3D Secure challenge required (SCA URL: {sca_url}). "
                  "Automated booking cannot complete 3DS without a browser.",
        )

    if tx_status != "ok" or error_msg:
        await asyncio.to_thread(effective_client.remove_from_cart, cart_item_ids, user_id)
        return BookingResult(
            success=False,
            error=error_msg or f"Authorisation failed with status '{tx_status}'.",
        )

    return BookingResult(success=True, reference=tx_uuid)


async def book_court_clubspark(
    venue: str,
    date: datetime.date,
    court_name: str,
    start_time: datetime.time,
    *,
    email: str,
    password: str,
    card: CardDetails | None = None,
    client: ClubSparkClient | None = None,
) -> BookingResult:
    """Book a specific ClubSpark court slot using LTA account credentials.

    Orchestrates the full browser-automation booking flow via
    :meth:`~personal_project.clients.clubspark.ClubSparkClient.book_slot`:

    1. Launch a headless Chromium browser (restoring a saved session when
       available).
    2. Authenticate with LTA via WS-Federation if the session is stale.
    3. Locate and click the booking link for *court_name* at *start_time*.
    4. Click "Continue booking" — ClubSpark navigates to the payment page.
    5. If *card* is provided, fill the Stripe Elements form and submit.
    6. Return a :class:`~models.BookingResult` with the reference string.

    Args:
        venue: ClubSpark venue slug, e.g. ``"BurgessParkSouthwark"``.
        date: The date of the slot to book.
        court_name: Exact court name as shown in the booking grid, e.g.
            ``"Crt 1"``.
        start_time: The start time of the slot to book.
        email: LTA account email address.
        password: LTA account password.
        card: Optional payment card details.  Required when the slot charges
            a booking fee.  When ``None`` and a Stripe form is encountered,
            the booking returns a failure result.
        client: Optional pre-constructed
            :class:`~personal_project.clients.clubspark.ClubSparkClient`
            instance.  When ``None`` a default client is created.

    Returns:
        A :class:`~models.BookingResult` with :attr:`~BookingResult.success`
        set to ``True`` on success, or ``False`` with an error message on
        failure.

    """
    effective_client = client or ClubSparkClient()
    try:
        reference = await effective_client.book_slot(
            venue, date, court_name, start_time,
            email=email, password=password,
            card_number=card.card_number if card else None,
            expiry_mmyy=card.expiry_mmyy if card else None,
            security_code=card.security_code if card else None,
        )
    except (RuntimeError, TimeoutError) as exc:
        return BookingResult(success=False, error=str(exc))
    return BookingResult(success=True, reference=reference)


async def check_slot_availability(
    venue: str,
    date: datetime.date,
    court_name: str,
    start_time: datetime.time,
    *,
    client: ClubSparkClient | None = None,
) -> bool:
    """Check whether a specific ClubSpark court slot is still available to book.

    Fetch the full booking grid for *venue* and *date*, then look for a
    slot matching *court_name* and *start_time*.

    Args:
        venue: ClubSpark venue slug, e.g. ``"BurgessParkSouthwark"``.
        date: The date to check.
        court_name: Exact court name as shown in the booking grid, e.g.
            ``"Court 1"``.
        start_time: The start time of the slot to verify.
        client: Optional
            :class:`~personal_project.clients.clubspark.ClubSparkClient`
            instance.  When ``None`` a default client is created.

    Returns:
        ``True`` if the slot exists and is available, ``False`` otherwise.

    Raises:
        TimeoutError: If the booking grid does not load within the client
            timeout.
        ValueError: If the page structure cannot be interpreted.

    """
    effective_client = client or ClubSparkClient()
    return await effective_client.is_slot_available(venue, date, court_name, start_time)
