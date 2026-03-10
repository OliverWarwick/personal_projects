"""Integration tests for the tennis court booker application.

These tests drive a real headless browser against the live ClubSpark website
to verify that the DOM selectors, attribute names, and parsing logic produce
correct results against actual page data.

Run with::

    uv run pytest -m integration

Tests are excluded from the standard suite (``uv run pytest``) to avoid
network dependencies in CI and fast-feedback loops.
"""

from __future__ import annotations

import datetime

import pytest

from personal_project.apps.tennis_court_booker.models import CourtSlot, VenueAvailability
from personal_project.apps.tennis_court_booker.service import (
    check_slot_availability,
    get_venue_availability,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_VENUE = "BurgessParkSouthwark"
_TOMORROW = datetime.datetime.now(tz=datetime.UTC).date() + datetime.timedelta(days=1)


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
class TestGetVenueAvailabilityIntegration:
    """Integration tests for get_venue_availability against the live ClubSpark site."""

    async def test_returns_venue_availability_for_tomorrow(self) -> None:
        """Return a VenueAvailability with the correct venue and date for tomorrow.

        Verifies the page loads, the grid renders, and the returned object has
        the expected shape.
        """
        result = await get_venue_availability(_VENUE, _TOMORROW)

        assert isinstance(result, VenueAvailability)
        assert result.venue == _VENUE
        assert result.date == _TOMORROW

    async def test_slots_are_court_slot_instances(self) -> None:
        """Verify every slot in the availability result is a CourtSlot."""
        result = await get_venue_availability(_VENUE, _TOMORROW)

        for slot in result.slots:
            assert isinstance(slot, CourtSlot)

    async def test_slots_have_valid_times(self) -> None:
        """Verify every slot has a start_time strictly before its end_time."""
        result = await get_venue_availability(_VENUE, _TOMORROW)

        assert result.slots, "Expected at least one slot from the live page"
        for slot in result.slots:
            assert slot.start_time < slot.end_time, (
                f"Slot {slot.court_name} {slot.start_time} has end_time <= start_time"
            )

    async def test_check_first_available_slot_is_still_available(self) -> None:
        """If any slot is available, confirm check_slot_availability agrees.

        Fetches the full availability grid, takes the first available slot,
        then calls check_slot_availability to independently verify it is
        bookable.  Both calls use the same real browser session so the result
        should match unless the court is booked between the two requests.
        """
        result = await get_venue_availability(_VENUE, _TOMORROW)

        available_slots = result.available_slots
        if not available_slots:
            pytest.skip(f"No available slots at {_VENUE} on {_TOMORROW} — nothing to check.")

        first = available_slots[0]
        is_available = await check_slot_availability(
            _VENUE,
            _TOMORROW,
            first.court_name,
            first.start_time,
        )

        assert is_available is True, (
            f"check_slot_availability returned False for {first.court_name} "
            f"at {first.start_time} on {_TOMORROW}, but get_venue_availability "
            "reported it as available."
        )
