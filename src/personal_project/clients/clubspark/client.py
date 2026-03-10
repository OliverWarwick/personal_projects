"""ClubSpark browser-automation client.

Uses Playwright (headless Chromium) to load the ClubSpark booking page and
extract court-availability data from the dynamically-rendered DOM.

ClubSpark's booking interface is a single-page application: the page shell is
served as static HTML, but the booking grid is injected by JavaScript after
the page reads the ``#?date=...&role=...`` URL fragment.  Simple HTTP requests
cannot retrieve the grid, so we drive a real browser instead.

DOM structure (as of 2026-03)
------------------------------
The booking grid is a ``.booking-sheet`` container.  Inside it, each court is
represented by a ``div.resource`` element carrying a ``data-resource-name``
attribute.  Within each resource, individual time slots are
``div.resource-session`` elements whose ``data-start-time`` and
``data-end-time`` attributes hold the slot boundaries as **minutes from
midnight** (e.g. 540 = 09:00).  Availability is signalled by
``data-availability="true"`` and the price by ``data-session-cost``.

If ClubSpark changes its front-end, update the ``_SELECTOR_*`` and
``_ATTR_*`` constants near the top of this module.
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from playwright.async_api import async_playwright

if TYPE_CHECKING:
    from playwright.async_api import Browser, ElementHandle, Page

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Selector constants - update here if ClubSpark changes its DOM
# ---------------------------------------------------------------------------

# The outer wrapper that appears once the booking grid has been rendered.
_SELECTOR_GRID_ROOT: Final = ".booking-sheet"

# Each court column is a div.resource element carrying a data-resource-name.
_SELECTOR_RESOURCE: Final = "div.resource[data-resource-name]"

# Each 30-minute bookable interval within a court column.  Intervals live
# inside resource-session elements, but the interval is the atomic bookable
# unit: an available session can contain many consecutive intervals.
_SELECTOR_INTERVAL: Final = "div.resource-interval"

# Each bookable session within a court column.  Sessions carry the start/end
# time boundaries and availability attributes parsed by _parse_session.
_SELECTOR_SESSION: Final = "div.resource-session"

# The booking anchor present inside an available interval.
_SELECTOR_BOOK_LINK: Final = "a.book-interval"

# Price span inside an available booking anchor.
_SELECTOR_COST: Final = "span.cost"

# ---------------------------------------------------------------------------
# Attribute constants on resource / session elements
# ---------------------------------------------------------------------------

# Court name on the resource div.
_ATTR_RESOURCE_NAME: Final = "data-resource-name"

# Slot boundaries on the session div (minutes from midnight).
_ATTR_START_TIME: Final = "data-start-time"
_ATTR_END_TIME: Final = "data-end-time"

# Availability flag on the session div.
_ATTR_AVAILABILITY: Final = "data-availability"

# String value of data-availability when the slot is bookable.
_AVAILABLE_VALUE: Final = "true"

# Optional price string on the session div.
_ATTR_SESSION_COST: Final = "data-session-cost"

# CSS class present on the booking anchor when the interval is still bookable.
_CSS_NOT_BOOKED: Final = "not-booked"

# Timeout (ms) to wait for the booking grid to appear after navigation.
_GRID_LOAD_TIMEOUT_MS: Final = 15_000

# Base URL of the ClubSpark LTA portal.
_BASE_URL: Final = "https://clubspark.lta.org.uk"


# ---------------------------------------------------------------------------
# Raw data transfer objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RawSlot:
    """Raw slot data parsed directly from the DOM before domain modelling.

    Args:
        court_name: The human-readable name of the court (e.g. ``"Crt 1"``).
        start_time: The start time of the slot.
        end_time: The end time of the slot.
        is_available: ``True`` if the slot can be booked.
        price: Optional price string scraped from the session element
            (e.g. ``"3.60"``).

    """

    court_name: str
    start_time: datetime.time
    end_time: datetime.time
    is_available: bool
    price: str | None = None


# ---------------------------------------------------------------------------
# Public client
# ---------------------------------------------------------------------------


class ClubSparkClient:
    """Async client for scraping court availability from ClubSpark.

    Launch a headless Chromium browser via Playwright, navigate to the
    ClubSpark booking-by-date page for the requested venue, wait for the
    booking grid to render, and then parse the DOM to return structured slot
    data.

    Usage::

        client = ClubSparkClient()
        slots = await client.get_available_slots(
            "BurgessParkSouthwark",
            datetime.date(2026, 3, 10),
        )

    The client is stateless: a new browser context is opened and closed for
    every call, so instances can be reused freely.
    """

    def __init__(self, *, headless: bool = True, timeout_ms: int = _GRID_LOAD_TIMEOUT_MS) -> None:
        """Initialise the client.

        Args:
            headless: Run Chromium in headless mode.  Set to ``False`` when
                debugging selector issues so you can observe the browser.
            timeout_ms: Maximum time in milliseconds to wait for the booking
                grid to appear after navigation.

        """
        self._headless = headless
        self._timeout_ms = timeout_ms

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_available_slots(
        self,
        venue: str,
        date: datetime.date,
    ) -> list[RawSlot]:
        """Fetch all court slots (available and unavailable) for a venue on a date.

        Navigate to the ClubSpark booking page for *venue* on *date* as a
        guest, wait for the booking grid to render, and return every slot found
        in the grid, regardless of availability status.

        Args:
            venue: The venue slug as it appears in the ClubSpark URL, e.g.
                ``"BurgessParkSouthwark"``.
            date: The date for which to fetch availability.

        Returns:
            A list of :class:`RawSlot` instances, one per (court, time-slot)
            combination found in the booking grid.

        Raises:
            TimeoutError: If the booking grid does not render within
                ``timeout_ms`` milliseconds.
            ValueError: If no courts can be parsed from the page.

        """
        async with async_playwright() as playwright:
            browser: Browser = await playwright.chromium.launch(headless=self._headless)
            try:
                page: Page = await browser.new_page()
                return await self._scrape_slots(page, venue, date)
            finally:
                await browser.close()

    async def is_slot_available(
        self,
        venue: str,
        date: datetime.date,
        court_name: str,
        start_time: datetime.time,
    ) -> bool:
        """Check whether a specific court slot is still bookable.

        This is a convenience wrapper around :meth:`get_available_slots` that
        filters to the requested (court, start_time) combination.

        Args:
            venue: ClubSpark venue slug (e.g. ``"BurgessParkSouthwark"``).
            date: The date to check.
            court_name: Exact court name as shown in the booking grid.
            start_time: The start time of the slot to check.

        Returns:
            ``True`` if the slot exists in the grid and is currently available,
            ``False`` otherwise (including when the slot does not exist).

        """
        slots = await self.get_available_slots(venue, date)
        for slot in slots:
            if slot.court_name == court_name and slot.start_time == start_time:
                return slot.is_available
        return False

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _scrape_slots(
        self,
        page: Page,
        venue: str,
        date: datetime.date,
    ) -> list[RawSlot]:
        """Navigate to the booking page and parse the rendered grid.

        Args:
            page: An open Playwright :class:`Page` instance.
            venue: ClubSpark venue slug.
            date: Date for which to retrieve the booking grid.

        Returns:
            Parsed list of :class:`RawSlot` objects.

        Raises:
            TimeoutError: If the grid selector does not appear in time.
            ValueError: If no courts can be parsed from the grid.

        """
        url = f"{_BASE_URL}/{venue}/Booking/BookByDate#?date={date.isoformat()}&role=guest"
        logger.debug("Navigating to %s", url)
        await page.goto(url, wait_until="domcontentloaded")

        try:
            await page.wait_for_selector(_SELECTOR_GRID_ROOT, timeout=self._timeout_ms)
        except Exception as exc:
            msg = (
                f"Booking grid did not appear within {self._timeout_ms} ms for venue "
                f"'{venue}' on {date}.  The selector '{_SELECTOR_GRID_ROOT}' was not found. "
                "The page may require login, or ClubSpark may have changed its DOM structure."
            )
            raise TimeoutError(msg) from exc

        resources = await page.query_selector_all(_SELECTOR_RESOURCE)
        if not resources:
            msg = (
                f"No courts found on the booking page for venue '{venue}' on {date}. "
                f"Expected elements matching '{_SELECTOR_RESOURCE}'."
            )
            raise ValueError(msg)

        slots = await self._parse_resources(resources)
        logger.debug("Parsed %d slot(s) for %s on %s", len(slots), venue, date)
        return slots

    async def _parse_resources(self, resources: list[ElementHandle]) -> list[RawSlot]:
        """Parse all court resources into a flat list of :class:`RawSlot` objects.

        Iterates over each ``.resource`` element, reads the court name from
        ``data-resource-name``, then delegates each ``.resource-session``
        within it to :meth:`_parse_session`.

        Args:
            resources: List of ``.resource`` element handles from the DOM.

        Returns:
            Flat list of all slots found across every court.

        """
        slots: list[RawSlot] = []
        for resource in resources:
            court_name = await resource.get_attribute(_ATTR_RESOURCE_NAME) or ""
            if not court_name:
                continue
            sessions = await resource.query_selector_all(_SELECTOR_SESSION)
            for session in sessions:
                slot = await self._parse_session(court_name, session)
                if slot is not None:
                    slots.append(slot)
        return slots

    async def _parse_session(
        self,
        court_name: str,
        session: ElementHandle,
    ) -> RawSlot | None:
        """Parse a single ``.resource-session`` element into a :class:`RawSlot`.

        Reads ``data-start-time``, ``data-end-time``, ``data-availability``,
        and ``data-session-cost`` from the element.  Start and end times are
        stored as minutes from midnight; when ``data-end-time`` is absent the
        end time is inferred as 60 minutes after the start.

        Args:
            court_name: The court name already extracted from the parent
                resource element.
            session: The ``.resource-session`` element handle to parse.

        Returns:
            A populated :class:`RawSlot`, or ``None`` if the element is
            missing the required ``data-start-time`` attribute.

        """
        start_attr = await session.get_attribute(_ATTR_START_TIME)
        if start_attr is None:
            return None

        try:
            start_time = _minutes_to_time(int(start_attr))
        except (ValueError, TypeError):
            return None

        end_attr = await session.get_attribute(_ATTR_END_TIME)
        if end_attr is not None:
            try:
                end_time = _minutes_to_time(int(end_attr))
            except (ValueError, TypeError):
                end_time = _infer_end_time(start_time)
        else:
            end_time = _infer_end_time(start_time)

        avail_attr = await session.get_attribute(_ATTR_AVAILABILITY)
        is_available = avail_attr == _AVAILABLE_VALUE

        cost_attr = await session.get_attribute(_ATTR_SESSION_COST)
        price: str | None = (cost_attr.strip() or None) if cost_attr is not None else None

        return RawSlot(
            court_name=court_name,
            start_time=start_time,
            end_time=end_time,
            is_available=is_available,
            price=price,
        )


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _minutes_to_time(minutes: int) -> datetime.time:
    """Convert a minutes-from-midnight integer to a :class:`datetime.time`.

    ClubSpark encodes slot boundaries as integers representing elapsed minutes
    since midnight (e.g. 420 → 07:00, 1020 → 17:00).

    Args:
        minutes: Non-negative integer of minutes elapsed since midnight.

    Returns:
        The corresponding :class:`datetime.time`.

    Raises:
        ValueError: If *minutes* produces an invalid time (e.g. negative or
            greater than 1439).

    """
    return datetime.time(minutes // 60, minutes % 60)


def _infer_end_time(start_time: datetime.time, duration_minutes: int = 60) -> datetime.time:
    """Derive an end time by adding *duration_minutes* to *start_time*.

    Used as a fallback when the session element does not carry an explicit
    ``data-end-time`` attribute.

    Args:
        start_time: The start time of the slot.
        duration_minutes: Duration to add in minutes (default 60).

    Returns:
        The inferred end time.

    """
    base = datetime.datetime(
        2000, 1, 1,
        start_time.hour, start_time.minute, start_time.second,
        tzinfo=datetime.UTC,
    )
    result = base + datetime.timedelta(minutes=duration_minutes)
    return result.time()
