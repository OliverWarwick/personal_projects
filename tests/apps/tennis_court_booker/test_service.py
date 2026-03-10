"""Tests for the tennis court booker service layer.

Verifies that :func:`get_venue_availability`, :func:`get_venue_availability_better`,
and :func:`check_slot_availability` correctly orchestrate their respective clients
and translate raw slot data into domain objects.  All network clients are replaced
with mocks so these tests run without a browser or network connection.
"""

from __future__ import annotations

import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from personal_project.apps.tennis_court_booker.models import CourtSlot, VenueAvailability
from personal_project.apps.tennis_court_booker.service import (
    check_slot_availability,
    get_venue_availability,
    get_venue_availability_better,
)
from personal_project.clients.clubspark.client import RawSlot

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

VENUE = "BurgessParkSouthwark"
DATE = datetime.date(2026, 3, 10)
TIME_09 = datetime.time(9, 0)
TIME_10 = datetime.time(10, 0)
TIME_11 = datetime.time(11, 0)
EXPECTED_SLOT_COUNT = 2


def _raw(
    court: str,
    start: datetime.time,
    end: datetime.time,
    *,
    available: bool,
) -> RawSlot:
    """Build a RawSlot for use in test doubles.

    Args:
        court: Court name string.
        start: Slot start time.
        end: Slot end time.
        available: Whether the slot is available.

    Returns:
        A :class:`RawSlot` instance.

    """
    return RawSlot(court_name=court, start_time=start, end_time=end, is_available=available)


@pytest.fixture
def mock_client() -> MagicMock:
    """Return a MagicMock standing in for ClubSparkClient.

    The ``get_available_slots`` and ``is_slot_available`` methods are
    configured as :class:`~unittest.mock.AsyncMock` so they can be awaited.

    Returns:
        A :class:`~unittest.mock.MagicMock` with async method doubles.

    """
    client = MagicMock()
    client.get_available_slots = AsyncMock()
    client.is_slot_available = AsyncMock()
    return client


# ---------------------------------------------------------------------------
# get_venue_availability tests
# ---------------------------------------------------------------------------


class TestGetVenueAvailability:
    """Tests for the get_venue_availability service function."""

    @pytest.mark.asyncio
    async def test_returns_venue_availability_instance(
        self, mock_client: MagicMock
    ) -> None:
        """Return a VenueAvailability object."""
        mock_client.get_available_slots.return_value = []
        result = await get_venue_availability(VENUE, DATE, client=mock_client)
        assert isinstance(result, VenueAvailability)

    @pytest.mark.asyncio
    async def test_venue_and_date_propagated(self, mock_client: MagicMock) -> None:
        """Propagate venue and date to the returned VenueAvailability."""
        mock_client.get_available_slots.return_value = []
        result = await get_venue_availability(VENUE, DATE, client=mock_client)
        assert result.venue == VENUE
        assert result.date == DATE

    @pytest.mark.asyncio
    async def test_raw_slots_converted_to_court_slots(
        self, mock_client: MagicMock
    ) -> None:
        """Convert all raw slots from the client into CourtSlot domain objects."""
        raw_slots = [
            _raw("Court 1", TIME_09, TIME_10, available=True),
            _raw("Court 2", TIME_09, TIME_10, available=False),
        ]
        mock_client.get_available_slots.return_value = raw_slots
        result = await get_venue_availability(VENUE, DATE, client=mock_client)
        assert len(result.slots) == EXPECTED_SLOT_COUNT
        assert all(isinstance(s, CourtSlot) for s in result.slots)

    @pytest.mark.asyncio
    async def test_date_attached_to_slots(self, mock_client: MagicMock) -> None:
        """Set the date field on each converted CourtSlot."""
        mock_client.get_available_slots.return_value = [
            _raw("Court 1", TIME_09, TIME_10, available=True),
        ]
        result = await get_venue_availability(VENUE, DATE, client=mock_client)
        assert result.slots[0].date == DATE

    @pytest.mark.asyncio
    async def test_availability_flag_preserved(self, mock_client: MagicMock) -> None:
        """Preserve the is_available flag when converting raw to domain slots."""
        mock_client.get_available_slots.return_value = [
            _raw("Court 1", TIME_09, TIME_10, available=True),
            _raw("Court 1", TIME_10, TIME_11, available=False),
        ]
        result = await get_venue_availability(VENUE, DATE, client=mock_client)
        assert result.slots[0].is_available is True
        assert result.slots[1].is_available is False

    @pytest.mark.asyncio
    async def test_client_called_with_venue_and_date(
        self, mock_client: MagicMock
    ) -> None:
        """Pass the correct venue and date arguments to the client."""
        mock_client.get_available_slots.return_value = []
        await get_venue_availability(VENUE, DATE, client=mock_client)
        mock_client.get_available_slots.assert_called_once_with(VENUE, DATE)

    @pytest.mark.asyncio
    async def test_empty_grid_returns_empty_slots(
        self, mock_client: MagicMock
    ) -> None:
        """Return a VenueAvailability with an empty slots list when the grid is empty."""
        mock_client.get_available_slots.return_value = []
        result = await get_venue_availability(VENUE, DATE, client=mock_client)
        assert result.slots == []


# ---------------------------------------------------------------------------
# check_slot_availability tests
# ---------------------------------------------------------------------------


class TestCheckSlotAvailability:
    """Tests for the check_slot_availability service function."""

    @pytest.mark.asyncio
    async def test_returns_true_when_available(self, mock_client: MagicMock) -> None:
        """Return True when the client reports the slot is available."""
        mock_client.is_slot_available.return_value = True
        result = await check_slot_availability(
            VENUE, DATE, "Court 1", TIME_09, client=mock_client
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_unavailable(
        self, mock_client: MagicMock
    ) -> None:
        """Return False when the client reports the slot is not available."""
        mock_client.is_slot_available.return_value = False
        result = await check_slot_availability(
            VENUE, DATE, "Court 1", TIME_09, client=mock_client
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_client_called_with_correct_args(
        self, mock_client: MagicMock
    ) -> None:
        """Pass venue, date, court name, and start time to the client."""
        mock_client.is_slot_available.return_value = True
        await check_slot_availability(
            VENUE, DATE, "Court 1", TIME_09, client=mock_client
        )
        mock_client.is_slot_available.assert_called_once_with(
            VENUE, DATE, "Court 1", TIME_09
        )


# ---------------------------------------------------------------------------
# get_venue_availability_better tests
# ---------------------------------------------------------------------------

BETTER_VENUE = "islington-tennis-centre"
BETTER_ACTIVITY = "tennis-court-indoor"

_SAMPLE_RAW_SLOT: dict[str, object] = {
    "date": "2026-03-10",
    "start_iso": "2026-03-10T09:00:00",
    "end_iso": "2026-03-10T10:00:00",
    "court_name": "Tennis Court - Indoor",
    "court_id": "ck1",
    "is_available": True,
    "price": "£40.00",
    "source_endpoint": "https://better-admin.org.uk/...",
}


@pytest.fixture
def mock_better_client() -> MagicMock:
    """Return a MagicMock standing in for BetterClient.

    ``ensure_logged_in`` returns ``True`` and ``get_availability`` returns a
    single sample slot.  Both are synchronous methods (the service wraps them
    in :func:`asyncio.to_thread`).

    Returns:
        A :class:`~unittest.mock.MagicMock` configured for happy-path testing.

    """
    client = MagicMock()
    client.ensure_logged_in.return_value = True
    client.get_availability.return_value = [dict(_SAMPLE_RAW_SLOT)]
    return client


class TestGetVenueAvailabilityBetter:
    """Tests for the get_venue_availability_better service function."""

    @pytest.mark.asyncio
    async def test_returns_venue_availability_instance(
        self, mock_better_client: MagicMock
    ) -> None:
        """Return a VenueAvailability object."""
        result = await get_venue_availability_better(
            BETTER_VENUE, BETTER_ACTIVITY, DATE, client=mock_better_client
        )
        assert isinstance(result, VenueAvailability)

    @pytest.mark.asyncio
    async def test_venue_and_date_propagated(self, mock_better_client: MagicMock) -> None:
        """Propagate venue and date to the returned VenueAvailability."""
        result = await get_venue_availability_better(
            BETTER_VENUE, BETTER_ACTIVITY, DATE, client=mock_better_client
        )
        assert result.venue == BETTER_VENUE
        assert result.date == DATE

    @pytest.mark.asyncio
    async def test_raw_slots_converted_to_court_slots(
        self, mock_better_client: MagicMock
    ) -> None:
        """Convert Better.com raw dicts into CourtSlot domain objects."""
        result = await get_venue_availability_better(
            BETTER_VENUE, BETTER_ACTIVITY, DATE, client=mock_better_client
        )
        assert len(result.slots) == 1
        assert isinstance(result.slots[0], CourtSlot)
        assert result.slots[0].court_name == "Tennis Court - Indoor"
        assert result.slots[0].start_time == TIME_09
        assert result.slots[0].end_time == TIME_10

    @pytest.mark.asyncio
    async def test_slot_with_missing_start_iso_is_skipped(
        self, mock_better_client: MagicMock
    ) -> None:
        """Skip raw slots that are missing start_iso."""
        bad = dict(_SAMPLE_RAW_SLOT)
        bad["start_iso"] = None
        mock_better_client.get_availability.return_value = [bad]
        result = await get_venue_availability_better(
            BETTER_VENUE, BETTER_ACTIVITY, DATE, client=mock_better_client
        )
        assert result.slots == []

    @pytest.mark.asyncio
    async def test_raises_runtime_error_when_login_fails(
        self, mock_better_client: MagicMock
    ) -> None:
        """Raise RuntimeError when ensure_logged_in returns False."""
        mock_better_client.ensure_logged_in.return_value = False
        with pytest.raises(RuntimeError, match="authentication failed"):
            await get_venue_availability_better(
                BETTER_VENUE, BETTER_ACTIVITY, DATE, client=mock_better_client
            )

    @pytest.mark.asyncio
    async def test_hours_filter_applied(self, mock_better_client: MagicMock) -> None:
        """Exclude slots whose start hour is not in the hours filter."""
        slot_09 = dict(_SAMPLE_RAW_SLOT)  # starts at 09:00 -> hour 9
        slot_17 = dict(_SAMPLE_RAW_SLOT)
        slot_17["start_iso"] = "2026-03-10T17:00:00"
        slot_17["end_iso"] = "2026-03-10T18:00:00"
        mock_better_client.get_availability.return_value = [slot_09, slot_17]
        result = await get_venue_availability_better(
            BETTER_VENUE, BETTER_ACTIVITY, DATE, hours=[17], client=mock_better_client
        )
        assert len(result.slots) == 1
        assert result.slots[0].start_time.hour == 17  # noqa: PLR2004

    @pytest.mark.asyncio
    async def test_empty_availability_returns_empty_slots(
        self, mock_better_client: MagicMock
    ) -> None:
        """Return empty slots list when the API returns no data."""
        mock_better_client.get_availability.return_value = []
        result = await get_venue_availability_better(
            BETTER_VENUE, BETTER_ACTIVITY, DATE, client=mock_better_client
        )
        assert result.slots == []
