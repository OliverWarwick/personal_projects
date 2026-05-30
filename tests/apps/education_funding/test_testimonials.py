"""Tests for the seeded testimonial roster."""

from __future__ import annotations

import datetime as dt

from personal_project.apps.education_funding.web.testimonials import get_testimonials

EXPECTED_TESTIMONIAL_COUNT = 3
RECENT_HISTORY_YEARS = 4


class TestGetTestimonials:
    """Tests for ``get_testimonials``."""

    def test_returns_three_testimonials(self) -> None:
        """The seeded roster has three entries."""
        assert len(get_testimonials()) == EXPECTED_TESTIMONIAL_COUNT

    def test_sorted_most_recent_first(self) -> None:
        """Testimonials are sorted by ``year_completed`` descending."""
        years = [t.year_completed for t in get_testimonials()]

        assert years == sorted(years, reverse=True)

    def test_all_dates_within_recent_window(self) -> None:
        """Every testimonial sits within the last four years."""
        cutoff = dt.date.today().year - RECENT_HISTORY_YEARS  # noqa: DTZ011 - calendar year is timezone-irrelevant
        for testimonial in get_testimonials():
            assert testimonial.year_completed >= cutoff

    def test_each_testimonial_has_quote_and_outcome(self) -> None:
        """Every testimonial carries non-empty quote and outcome strings."""
        for t in get_testimonials():
            assert t.quote
            assert t.outcome
            assert t.name
