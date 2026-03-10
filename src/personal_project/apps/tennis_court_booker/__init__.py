"""Tennis court booker application.

Provides high-level functions for querying court availability on ClubSpark
venues.  The two main entry points are:

* :func:`get_venue_availability` - retrieve all court slots (available and
  unavailable) for a venue on a given date, returned as a
  :class:`~personal_project.apps.tennis_court_booker.models.VenueAvailability`
  summary.

* :func:`check_slot_availability` - quickly confirm whether a specific court
  and time slot is still bookable.
"""

from personal_project.apps.tennis_court_booker.models import CourtSlot, VenueAvailability
from personal_project.apps.tennis_court_booker.service import (
    check_slot_availability,
    get_venue_availability,
)

__all__ = [
    "CourtSlot",
    "VenueAvailability",
    "check_slot_availability",
    "get_venue_availability",
]
