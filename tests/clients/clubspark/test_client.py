"""Tests for the ClubSpark browser-automation client.

All Playwright browser interactions are replaced with mocks so that these
tests run without launching a real browser or making network requests.  The
tests focus on the client's internal parsing and orchestration logic.
"""

from __future__ import annotations

import datetime
from unittest.mock import AsyncMock, patch

import pytest

from personal_project.clients.clubspark.client import (
    ClubSparkClient,
    RawSlot,
    _infer_end_time,
    _minutes_to_time,
)

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

VENUE = "BurgessParkSouthwark"
DATE = datetime.date(2026, 3, 10)
TIME_07 = datetime.time(7, 0)
TIME_07_30 = datetime.time(7, 30)
TIME_09 = datetime.time(9, 0)
TIME_10 = datetime.time(10, 0)
TIME_10_30 = datetime.time(10, 30)
TIME_17 = datetime.time(17, 0)
TIME_21 = datetime.time(21, 0)
CUSTOM_TIMEOUT_MS = 5_000

# Minutes-from-midnight values matching the times above.
MINUTES_07_00 = 420
MINUTES_07_30 = 450
MINUTES_09_00 = 540
MINUTES_10_00 = 600
MINUTES_10_30 = 630
MINUTES_17_00 = 1_020
MINUTES_21_00 = 1_260


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session_mock(
    start_min: int,
    end_min: int | None,
    *,
    available: bool,
    cost: str | None = None,
) -> AsyncMock:
    """Build a mock Playwright ElementHandle for a ``.resource-session`` div.

    Args:
        start_min: ``data-start-time`` value (minutes from midnight).
        end_min: ``data-end-time`` value, or ``None`` to omit the attribute.
        available: Whether ``data-availability`` should be ``"true"``.
        cost: ``data-session-cost`` value, or ``None`` to omit the attribute.

    Returns:
        An :class:`AsyncMock` that mimics a Playwright ``ElementHandle``.

    """
    session = AsyncMock()

    async def _get_attr(
        attr: str,
        *,
        _s: int = start_min,
        _e: int | None = end_min,
        _a: bool = available,
        _c: str | None = cost,
    ) -> str | None:
        if attr == "data-start-time":
            return str(_s)
        if attr == "data-end-time":
            return str(_e) if _e is not None else None
        if attr == "data-availability":
            return "true" if _a else "false"
        if attr == "data-session-cost":
            return _c
        return None

    session.get_attribute = _get_attr
    return session


def _make_resource_mock(
    court_name: str,
    sessions: list[AsyncMock],
) -> AsyncMock:
    """Build a mock Playwright ElementHandle for a ``.resource`` div.

    Args:
        court_name: Value of the ``data-resource-name`` attribute.
        sessions: Pre-built session mocks to return from
            ``query_selector_all``.

    Returns:
        An :class:`AsyncMock` that mimics a Playwright ``ElementHandle``.

    """
    resource = AsyncMock()

    async def _get_attr(attr: str, *, _name: str = court_name) -> str | None:
        if attr == "data-resource-name":
            return _name
        return None

    resource.get_attribute = _get_attr
    resource.query_selector_all = AsyncMock(return_value=sessions)
    return resource


def _make_page_mock(resources: list[AsyncMock]) -> AsyncMock:
    """Build a mock Playwright Page with configurable resource data.

    Args:
        resources: Pre-built resource mocks to return from
            ``query_selector_all``.

    Returns:
        An :class:`AsyncMock` that mimics a Playwright ``Page``.

    """
    page = AsyncMock()
    page.goto = AsyncMock(return_value=None)
    page.wait_for_selector = AsyncMock(return_value=None)
    page.query_selector_all = AsyncMock(return_value=resources)
    return page


# ---------------------------------------------------------------------------
# _minutes_to_time tests
# ---------------------------------------------------------------------------


class TestMinutesToTime:
    """Tests for the _minutes_to_time helper function."""

    def test_converts_zero_to_midnight(self) -> None:
        """Convert 0 to 00:00."""
        assert _minutes_to_time(0) == datetime.time(0, 0)

    def test_converts_420_to_seven_am(self) -> None:
        """Convert 420 (7 * 60) to 07:00."""
        assert _minutes_to_time(MINUTES_07_00) == TIME_07

    def test_converts_450_to_seven_thirty(self) -> None:
        """Convert 450 (7 * 60 + 30) to 07:30."""
        assert _minutes_to_time(MINUTES_07_30) == TIME_07_30

    def test_converts_540_to_nine_am(self) -> None:
        """Convert 540 (9 * 60) to 09:00."""
        assert _minutes_to_time(MINUTES_09_00) == TIME_09

    def test_converts_1020_to_five_pm(self) -> None:
        """Convert 1020 (17 * 60) to 17:00."""
        assert _minutes_to_time(MINUTES_17_00) == TIME_17

    def test_converts_1260_to_nine_pm(self) -> None:
        """Convert 1260 (21 * 60) to 21:00."""
        assert _minutes_to_time(MINUTES_21_00) == TIME_21


# ---------------------------------------------------------------------------
# _infer_end_time tests
# ---------------------------------------------------------------------------


class TestInferEndTime:
    """Tests for the _infer_end_time helper function."""

    def test_default_sixty_minutes(self) -> None:
        """Add 60 minutes to the start time by default."""
        assert _infer_end_time(datetime.time(9, 0)) == datetime.time(10, 0)

    def test_thirty_minutes(self) -> None:
        """Add the specified number of minutes to the start time."""
        assert _infer_end_time(datetime.time(9, 0), 30) == datetime.time(9, 30)

    def test_spans_hour_boundary(self) -> None:
        """Handle start times that produce an end time past the hour boundary."""
        assert _infer_end_time(datetime.time(9, 30), 60) == datetime.time(10, 30)


# ---------------------------------------------------------------------------
# ClubSparkClient initialisation tests
# ---------------------------------------------------------------------------


class TestClubSparkClientInit:
    """Tests for ClubSparkClient initialisation."""

    def test_default_headless_true(self) -> None:
        """Default to headless mode."""
        client = ClubSparkClient()
        assert client._headless is True

    def test_custom_headless(self) -> None:
        """Accept a custom headless flag."""
        client = ClubSparkClient(headless=False)
        assert client._headless is False

    def test_custom_timeout(self) -> None:
        """Accept a custom timeout value."""
        client = ClubSparkClient(timeout_ms=CUSTOM_TIMEOUT_MS)
        assert client._timeout_ms == CUSTOM_TIMEOUT_MS


# ---------------------------------------------------------------------------
# ClubSparkClient.is_slot_available tests
# ---------------------------------------------------------------------------


class TestClubSparkClientIsSlotAvailable:
    """Tests for ClubSparkClient.is_slot_available."""

    @pytest.mark.asyncio
    async def test_returns_true_when_available(self) -> None:
        """Return True when the client finds an available matching slot."""
        raw_slots = [RawSlot("Court 1", TIME_09, TIME_10, is_available=True)]
        client = ClubSparkClient()
        with patch.object(client, "get_available_slots", new=AsyncMock(return_value=raw_slots)):
            result = await client.is_slot_available(VENUE, DATE, "Court 1", TIME_09)
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_booked(self) -> None:
        """Return False when the matching slot is not available."""
        raw_slots = [RawSlot("Court 1", TIME_09, TIME_10, is_available=False)]
        client = ClubSparkClient()
        with patch.object(client, "get_available_slots", new=AsyncMock(return_value=raw_slots)):
            result = await client.is_slot_available(VENUE, DATE, "Court 1", TIME_09)
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_not_found(self) -> None:
        """Return False when no slot matches the requested court and time."""
        client = ClubSparkClient()
        with patch.object(client, "get_available_slots", new=AsyncMock(return_value=[])):
            result = await client.is_slot_available(VENUE, DATE, "Court 1", TIME_09)
        assert result is False


# ---------------------------------------------------------------------------
# ClubSparkClient.get_available_slots tests (Playwright mocked)
# ---------------------------------------------------------------------------


class TestClubSparkClientGetAvailableSlots:
    """Tests for ClubSparkClient.get_available_slots (Playwright mocked)."""

    @pytest.mark.asyncio
    async def test_delegates_to_scrape_slots(self) -> None:
        """Call _scrape_slots with the correct page, venue, and date arguments."""
        expected: list[RawSlot] = [RawSlot("Court 1", TIME_09, TIME_10, is_available=True)]
        client = ClubSparkClient()

        with patch("personal_project.clients.clubspark.client.async_playwright") as mock_pw_ctx:
            mock_browser = AsyncMock()
            mock_page = AsyncMock()
            mock_browser.new_page = AsyncMock(return_value=mock_page)
            mock_browser.close = AsyncMock()

            mock_pw_instance = AsyncMock()
            mock_pw_instance.chromium.launch = AsyncMock(return_value=mock_browser)
            mock_pw_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_pw_instance)
            mock_pw_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch.object(client, "_scrape_slots", new=AsyncMock(return_value=expected)):
                result = await client.get_available_slots(VENUE, DATE)

        assert result == expected


# ---------------------------------------------------------------------------
# ClubSparkClient._scrape_slots tests
# ---------------------------------------------------------------------------


class TestScrapeSlots:
    """Tests for ClubSparkClient._scrape_slots."""

    @pytest.mark.asyncio
    async def test_raises_timeout_when_grid_not_found(self) -> None:
        """Raise TimeoutError when wait_for_selector times out."""
        page = AsyncMock()
        page.goto = AsyncMock(return_value=None)
        page.wait_for_selector = AsyncMock(side_effect=Exception("Timeout"))
        client = ClubSparkClient()

        with pytest.raises(TimeoutError, match="Booking grid did not appear"):
            await client._scrape_slots(page, VENUE, DATE)

    @pytest.mark.asyncio
    async def test_raises_value_error_when_no_courts(self) -> None:
        """Raise ValueError when no resource elements are found on the page."""
        page = _make_page_mock(resources=[])
        client = ClubSparkClient()

        with pytest.raises(ValueError, match="No courts found"):
            await client._scrape_slots(page, VENUE, DATE)

    @pytest.mark.asyncio
    async def test_returns_slots_for_single_court(self) -> None:
        """Return one RawSlot for a single court with one available session."""
        session = _make_session_mock(MINUTES_09_00, MINUTES_10_00, available=True)
        resource = _make_resource_mock("Crt 1", [session])
        page = _make_page_mock([resource])
        client = ClubSparkClient()

        slots = await client._scrape_slots(page, VENUE, DATE)

        assert len(slots) == 1
        assert slots[0].court_name == "Crt 1"
        assert slots[0].start_time == TIME_09
        assert slots[0].end_time == TIME_10
        assert slots[0].is_available is True

    @pytest.mark.asyncio
    async def test_returns_slots_for_multiple_courts(self) -> None:
        """Return one slot per court when multiple courts are present."""
        two_slots = 2
        s1 = _make_session_mock(MINUTES_09_00, MINUTES_10_00, available=True)
        s2 = _make_session_mock(MINUTES_09_00, MINUTES_10_00, available=False)
        r1 = _make_resource_mock("Crt 1", [s1])
        r2 = _make_resource_mock("Crt 2", [s2])
        page = _make_page_mock([r1, r2])
        client = ClubSparkClient()

        slots = await client._scrape_slots(page, VENUE, DATE)

        assert len(slots) == two_slots
        assert slots[0].court_name == "Crt 1"
        assert slots[0].is_available is True
        assert slots[1].court_name == "Crt 2"
        assert slots[1].is_available is False

    @pytest.mark.asyncio
    async def test_skips_session_without_start_time(self) -> None:
        """Skip sessions whose data-start-time attribute is absent."""
        session = AsyncMock()
        session.get_attribute = AsyncMock(return_value=None)
        resource = _make_resource_mock("Crt 1", [session])
        page = _make_page_mock([resource])
        client = ClubSparkClient()

        slots = await client._scrape_slots(page, VENUE, DATE)
        assert slots == []

    @pytest.mark.asyncio
    async def test_uses_explicit_end_time(self) -> None:
        """Use data-end-time when present."""
        session = _make_session_mock(MINUTES_09_00, MINUTES_10_30, available=True)
        resource = _make_resource_mock("Crt 1", [session])
        page = _make_page_mock([resource])
        client = ClubSparkClient()

        slots = await client._scrape_slots(page, VENUE, DATE)

        assert len(slots) == 1
        assert slots[0].end_time == TIME_10_30

    @pytest.mark.asyncio
    async def test_infers_end_time_when_attribute_absent(self) -> None:
        """Infer end time as 60 minutes after start when data-end-time is absent."""
        session = _make_session_mock(MINUTES_09_00, None, available=True)
        resource = _make_resource_mock("Crt 1", [session])
        page = _make_page_mock([resource])
        client = ClubSparkClient()

        slots = await client._scrape_slots(page, VENUE, DATE)

        assert len(slots) == 1
        assert slots[0].end_time == TIME_10

    @pytest.mark.asyncio
    async def test_captures_price_from_session_cost(self) -> None:
        """Set price from data-session-cost when present."""
        session = _make_session_mock(MINUTES_09_00, MINUTES_10_00, available=True, cost="3.60")
        resource = _make_resource_mock("Crt 1", [session])
        page = _make_page_mock([resource])
        client = ClubSparkClient()

        slots = await client._scrape_slots(page, VENUE, DATE)

        assert len(slots) == 1
        assert slots[0].price == "3.60"

    @pytest.mark.asyncio
    async def test_price_none_when_no_cost_attribute(self) -> None:
        """Set price to None when data-session-cost is absent."""
        session = _make_session_mock(MINUTES_09_00, MINUTES_10_00, available=True)
        resource = _make_resource_mock("Crt 1", [session])
        page = _make_page_mock([resource])
        client = ClubSparkClient()

        slots = await client._scrape_slots(page, VENUE, DATE)

        assert len(slots) == 1
        assert slots[0].price is None

    @pytest.mark.asyncio
    async def test_returns_multiple_sessions_per_court(self) -> None:
        """Return all sessions for a court when multiple time slots exist."""
        three_slots = 3
        sessions = [
            _make_session_mock(MINUTES_07_00, MINUTES_07_30, available=True),
            _make_session_mock(MINUTES_07_30, MINUTES_09_00, available=False),
            _make_session_mock(MINUTES_09_00, MINUTES_10_00, available=True),
        ]
        resource = _make_resource_mock("Crt 1", sessions)
        page = _make_page_mock([resource])
        client = ClubSparkClient()

        slots = await client._scrape_slots(page, VENUE, DATE)
        assert len(slots) == three_slots
