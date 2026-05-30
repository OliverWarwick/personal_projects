"""Tests for the P2P cadet profile factory.

Verifies the deterministic seeding (so the UI doesn't shuffle on refresh)
and the funding-amount constraints (50 to 100% of training cost, in £1k
increments).
"""

from __future__ import annotations

from personal_project.apps.education_funding.web.cadets import get_cadet_profiles

PCT_TOLERANCE = 1e-6

EXPECTED_CADET_COUNT = 5
FUNDING_INCREMENT_GBP = 1_000
MIN_FUNDING_FRACTION = 0.50
MAX_FUNDING_FRACTION = 1.00


class TestCadetProfiles:
    """Tests for the canonical P2P cadet roster."""

    def test_returns_all_five_cv_cadets(self) -> None:
        """The factory surfaces all five example-CV cadets in stable order."""
        cadets = get_cadet_profiles()

        assert len(cadets) == EXPECTED_CADET_COUNT
        assert [c.name for c in cadets] == [
            "James Whitfield",
            "Priya Ramanathan",
            "Daniel Osei",
            "Sophie Hartley",
            "Connor McAllister",
        ]

    def test_airline_assignment_is_deterministic(self) -> None:
        """Calling the factory twice yields the same airline per cadet."""
        first = {c.slug: c.airline_programme.key for c in get_cadet_profiles()}
        second = {c.slug: c.airline_programme.key for c in get_cadet_profiles()}

        assert first == second

    def test_funding_amount_is_deterministic(self) -> None:
        """Calling the factory twice yields the same funding amount per cadet."""
        first = {c.slug: c.funding_sought_gbp for c in get_cadet_profiles()}
        second = {c.slug: c.funding_sought_gbp for c in get_cadet_profiles()}

        assert first == second

    def test_airline_is_easyjet_or_ryanair(self) -> None:
        """Airline assignment is restricted to the two configured programmes."""
        cadets = get_cadet_profiles()

        assert all(c.airline_programme.key in {"easyjet", "ryanair"} for c in cadets)

    def test_funding_falls_in_50_to_100_pct_band(self) -> None:
        """Funding sought sits in the 50-100% band of the programme cost."""
        for cadet in get_cadet_profiles():
            cost = cadet.airline_programme.training_cost_gbp
            assert cost * MIN_FUNDING_FRACTION <= cadet.funding_sought_gbp <= cost * MAX_FUNDING_FRACTION

    def test_funding_amount_uses_thousand_pound_increments(self) -> None:
        """Each funding amount is a clean multiple of £1,000."""
        for cadet in get_cadet_profiles():
            assert cadet.funding_sought_gbp % FUNDING_INCREMENT_GBP == 0

    def test_initials_are_two_uppercase_letters(self) -> None:
        """Each cadet exposes a two-letter uppercase initials string."""
        for cadet in get_cadet_profiles():
            assert len(cadet.initials) == 2  # noqa: PLR2004 - first/last initials
            assert cadet.initials.isupper()

    def test_avatar_colour_is_hex_string(self) -> None:
        """Each cadet exposes a ``#RRGGBB`` avatar colour."""
        for cadet in get_cadet_profiles():
            assert cadet.avatar_color_hex.startswith("#")
            assert len(cadet.avatar_color_hex) == len("#000000")

    def test_secured_never_exceeds_sought(self) -> None:
        """``funding_secured_gbp`` never exceeds ``funding_sought_gbp``."""
        for cadet in get_cadet_profiles():
            assert 0 <= cadet.funding_secured_gbp <= cadet.funding_sought_gbp

    def test_secured_uses_thousand_pound_increments(self) -> None:
        """Each secured amount is a clean multiple of £1,000."""
        for cadet in get_cadet_profiles():
            assert cadet.funding_secured_gbp % FUNDING_INCREMENT_GBP == 0


class TestFundingBreakdown:
    """Tests for the three-way funding split exposed on each cadet."""

    def test_breakdown_buckets_sum_to_total_cost(self) -> None:
        """Secured + remaining + self-funded always equals total cost."""
        for cadet in get_cadet_profiles():
            b = cadet.funding_breakdown
            assert b.secured_gbp + b.remaining_gbp + b.self_funded_gbp == b.total_cost_gbp

    def test_breakdown_percentages_sum_to_one_hundred(self) -> None:
        """The three percentage fields round-trip cleanly to 100%."""
        for cadet in get_cadet_profiles():
            b = cadet.funding_breakdown
            total = b.secured_pct + b.remaining_pct + b.self_funded_pct
            assert abs(total - 100.0) < PCT_TOLERANCE

    def test_remaining_is_sought_minus_secured(self) -> None:
        """``remaining_gbp`` is the gap between sought and secured."""
        for cadet in get_cadet_profiles():
            b = cadet.funding_breakdown
            assert b.remaining_gbp == b.sought_gbp - b.secured_gbp

    def test_self_funded_is_total_minus_sought(self) -> None:
        """``self_funded_gbp`` is the portion the cadet covers themselves."""
        for cadet in get_cadet_profiles():
            b = cadet.funding_breakdown
            assert b.self_funded_gbp == b.total_cost_gbp - b.sought_gbp
