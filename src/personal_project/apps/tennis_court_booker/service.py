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

from personal_project.apps.tennis_court_booker.models import CourtSlot, VenueAvailability
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
