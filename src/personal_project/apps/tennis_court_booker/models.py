"""Domain models for the tennis court booker application.

Defines the core data structures used to represent court slots and venue
availability.  All models are immutable value objects implemented as frozen
dataclasses, making them safe to cache, hash, and use as dictionary keys.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass


@dataclass(frozen=True)
class VenueConfig:
    """Configuration for a single bookable venue loaded from the YAML config.

    Carries the booking client type, the venue identifier required by that
    client, an optional activity slug (required for Better.com venues), and an
    optional human-readable display name used for output formatting.

    Args:
        client: The booking client to use, either ``"clubspark"`` or
            ``"better_com"``.
        venue: The venue slug as required by the client, e.g.
            ``"BurgessParkSouthwark"`` for ClubSpark or
            ``"islington-tennis-centre"`` for Better.com.
        activity: Activity slug for Better.com venues, e.g.
            ``"tennis-court-indoor"``.  ``None`` for ClubSpark venues.
        display_name: Human-readable label for output headers, e.g.
            ``"islington_indoor"``.  When ``None``, :attr:`venue` is used
            as the display label.

    """

    client: str
    venue: str
    activity: str | None = None
    display_name: str | None = None


@dataclass(frozen=True)
class CourtSlot:
    """A single bookable (or already-booked) time slot for one court.

    Represents the combination of a specific court, date, and time window at a
    ClubSpark venue.  Instances are immutable and hashable so they can be
    stored in sets or used as dict keys.

    Args:
        court_name: Human-readable court identifier, e.g. ``"Court 1"``.
        date: The calendar date on which the slot falls.
        start_time: The start time of the booking window.
        end_time: The end time of the booking window.
        is_available: ``True`` when the slot can still be booked by a guest.
        price: Optional price string as displayed on the booking page,
            e.g. ``"9.40"``.  ``None`` when no price information is shown.

    """

    court_name: str
    date: datetime.date
    start_time: datetime.time
    end_time: datetime.time
    is_available: bool
    price: str | None = None

    @property
    def duration_minutes(self) -> int:
        """Return the slot duration in whole minutes.

        Returns:
            The number of minutes between :attr:`start_time` and
            :attr:`end_time`.

        """
        start_dt = datetime.datetime.combine(self.date, self.start_time)
        end_dt = datetime.datetime.combine(self.date, self.end_time)
        return int((end_dt - start_dt).total_seconds() // 60)

    def __str__(self) -> str:
        """Return a compact human-readable representation of the slot.

        Returns:
            A string of the form ``"Court 1 09:00-10:00 (available)"``.

        """
        status = "available" if self.is_available else "unavailable"
        return (
            f"{self.court_name} "
            f"{self.start_time.strftime('%H:%M')}-{self.end_time.strftime('%H:%M')} "
            f"({status})"
        )


@dataclass
class VenueAvailability:
    """Aggregated availability summary for a venue on a specific date.

    Holds all court slots (available and booked) retrieved from the ClubSpark
    booking grid for a particular venue and date.  Provides convenience
    properties for filtering and summarising the data.

    Args:
        venue: The ClubSpark venue slug, e.g. ``"BurgessParkSouthwark"``.
        date: The date for which availability was retrieved.
        slots: All :class:`CourtSlot` objects found in the booking grid,
            including both available and unavailable slots.

    """

    venue: str
    date: datetime.date
    slots: list[CourtSlot]

    @property
    def available_slots(self) -> list[CourtSlot]:
        """Return only the slots that are currently available to book.

        Returns:
            A filtered list containing only :class:`CourtSlot` instances where
            :attr:`CourtSlot.is_available` is ``True``.

        """
        return [s for s in self.slots if s.is_available]

    @property
    def courts(self) -> list[str]:
        """Return an ordered, deduplicated list of court names in the grid.

        Returns:
            Court names in the order they first appear in :attr:`slots`.

        """
        seen: set[str] = set()
        result: list[str] = []
        for slot in self.slots:
            if slot.court_name not in seen:
                seen.add(slot.court_name)
                result.append(slot.court_name)
        return result

    @property
    def total_slots(self) -> int:
        """Return the total number of slots (available and unavailable).

        Returns:
            The length of :attr:`slots`.

        """
        return len(self.slots)

    @property
    def available_count(self) -> int:
        """Return the number of available slots.

        Returns:
            Count of slots where :attr:`CourtSlot.is_available` is ``True``.

        """
        return sum(1 for s in self.slots if s.is_available)

    def slots_for_court(self, court_name: str) -> list[CourtSlot]:
        """Return all slots (available and unavailable) for a specific court.

        Args:
            court_name: The exact court name to filter by.

        Returns:
            All :class:`CourtSlot` instances whose :attr:`CourtSlot.court_name`
            matches *court_name*.

        """
        return [s for s in self.slots if s.court_name == court_name]

    def available_slots_for_court(self, court_name: str) -> list[CourtSlot]:
        """Return only the available slots for a specific court.

        Args:
            court_name: The exact court name to filter by.

        Returns:
            Available :class:`CourtSlot` instances for *court_name*.

        """
        return [s for s in self.slots if s.court_name == court_name and s.is_available]

    def __str__(self) -> str:
        """Return a human-readable availability summary.

        Returns:
            A multi-line string listing the date, venue, total slot count,
            and available slot count.

        """
        return (
            f"Venue: {self.venue}\n"
            f"Date: {self.date}\n"
            f"Available: {self.available_count}/{self.total_slots} slots\n"
            f"Courts: {', '.join(self.courts)}"
        )
