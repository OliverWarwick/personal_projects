"""Tests for the tennis court booker domain models.

Covers the behaviour of :class:`CourtSlot` and :class:`VenueAvailability`,
including their properties, computed values, filtering helpers, and string
representations.
"""

from __future__ import annotations

import dataclasses
import datetime

import pytest

from personal_project.apps.tennis_court_booker.models import CourtSlot, VenueAvailability

# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

DATE = datetime.date(2026, 3, 10)
TIME_09 = datetime.time(9, 0)
TIME_10 = datetime.time(10, 0)
TIME_10_30 = datetime.time(10, 30)
TIME_11 = datetime.time(11, 0)

DURATION_60 = 60
DURATION_30 = 30
EXPECTED_AVAILABLE = 2
EXPECTED_TOTAL = 4
EXPECTED_COURT_SLOTS = 2


def _make_slot(
    court: str = "Court 1",
    start: datetime.time = TIME_09,
    end: datetime.time = TIME_10,
    *,
    available: bool = True,
    price: str | None = "9.40",
) -> CourtSlot:
    """Build a :class:`CourtSlot` with sensible defaults for testing.

    Args:
        court: Court name.
        start: Start time.
        end: End time.
        available: Whether the slot is available.
        price: Optional price string.

    Returns:
        A configured :class:`CourtSlot`.

    """
    return CourtSlot(
        court_name=court,
        date=DATE,
        start_time=start,
        end_time=end,
        is_available=available,
        price=price,
    )


# ---------------------------------------------------------------------------
# CourtSlot tests
# ---------------------------------------------------------------------------


class TestCourtSlot:
    """Tests for the CourtSlot domain model."""

    def test_duration_minutes_sixty(self) -> None:
        """Return 60 for a slot spanning a full hour."""
        slot = _make_slot(start=TIME_09, end=TIME_10)
        assert slot.duration_minutes == DURATION_60

    def test_duration_minutes_thirty(self) -> None:
        """Return 30 for a slot spanning half an hour."""
        slot = _make_slot(start=TIME_10, end=TIME_10_30)
        assert slot.duration_minutes == DURATION_30

    def test_str_available(self) -> None:
        """Include 'available' in string representation when slot is free."""
        slot = _make_slot(available=True)
        assert "available" in str(slot)
        assert "Court 1" in str(slot)
        assert "09:00" in str(slot)

    def test_str_unavailable(self) -> None:
        """Include 'unavailable' in string representation when slot is taken."""
        slot = _make_slot(available=False)
        assert "unavailable" in str(slot)

    def test_is_frozen(self) -> None:
        """Raise FrozenInstanceError when attempting to mutate a slot."""
        slot = _make_slot()
        with pytest.raises(dataclasses.FrozenInstanceError):
            slot.court_name = "Court 2"  # type: ignore[misc]

    def test_hashable(self) -> None:
        """Allow CourtSlot instances to be stored in a set."""
        slot_a = _make_slot()
        slot_b = _make_slot()
        assert slot_a == slot_b
        assert len({slot_a, slot_b}) == 1

    def test_price_none_by_default(self) -> None:
        """Accept None as price when no pricing information is available."""
        slot = CourtSlot(
            court_name="Court 1",
            date=DATE,
            start_time=TIME_09,
            end_time=TIME_10,
            is_available=True,
        )
        assert slot.price is None


# ---------------------------------------------------------------------------
# VenueAvailability tests
# ---------------------------------------------------------------------------


class TestVenueAvailability:
    """Tests for the VenueAvailability aggregate model."""

    def _build_availability(self) -> VenueAvailability:
        """Build a VenueAvailability with a mix of available and booked slots.

        Returns:
            A :class:`VenueAvailability` with four slots across two courts.

        """
        return VenueAvailability(
            venue="BurgessParkSouthwark",
            date=DATE,
            slots=[
                _make_slot("Court 1", TIME_09, TIME_10, available=True),
                _make_slot("Court 1", TIME_10, TIME_11, available=False),
                _make_slot("Court 2", TIME_09, TIME_10, available=False),
                _make_slot("Court 2", TIME_10, TIME_11, available=True),
            ],
        )

    def test_available_slots_filters_correctly(self) -> None:
        """Return only slots where is_available is True."""
        va = self._build_availability()
        avail = va.available_slots
        assert len(avail) == EXPECTED_AVAILABLE
        assert all(s.is_available for s in avail)

    def test_total_slots_count(self) -> None:
        """Return the total count of all slots regardless of availability."""
        va = self._build_availability()
        assert va.total_slots == EXPECTED_TOTAL

    def test_available_count(self) -> None:
        """Return the number of available slots."""
        va = self._build_availability()
        assert va.available_count == EXPECTED_AVAILABLE

    def test_courts_deduplicates_and_preserves_order(self) -> None:
        """Return unique court names in insertion order."""
        va = self._build_availability()
        assert va.courts == ["Court 1", "Court 2"]

    def test_slots_for_court_filters_by_name(self) -> None:
        """Return all slots (available and unavailable) for a named court."""
        va = self._build_availability()
        court1_slots = va.slots_for_court("Court 1")
        assert len(court1_slots) == EXPECTED_COURT_SLOTS
        assert all(s.court_name == "Court 1" for s in court1_slots)

    def test_slots_for_court_unknown_returns_empty(self) -> None:
        """Return an empty list for a court name not in the grid."""
        va = self._build_availability()
        assert va.slots_for_court("Court 99") == []

    def test_available_slots_for_court(self) -> None:
        """Return only available slots for the requested court."""
        va = self._build_availability()
        available = va.available_slots_for_court("Court 1")
        assert len(available) == 1
        assert available[0].start_time == TIME_09

    def test_str_contains_venue_and_date(self) -> None:
        """Include venue name and date in the string summary."""
        va = self._build_availability()
        summary = str(va)
        assert "BurgessParkSouthwark" in summary
        assert "2026-03-10" in summary

    def test_str_contains_availability_ratio(self) -> None:
        """Include available/total ratio in the string summary."""
        va = self._build_availability()
        summary = str(va)
        assert "2/4" in summary

    def test_empty_slots(self) -> None:
        """Handle an availability object with no slots gracefully."""
        va = VenueAvailability(venue="EmptyVenue", date=DATE, slots=[])
        assert va.available_slots == []
        assert va.courts == []
        assert va.total_slots == 0
        assert va.available_count == 0
