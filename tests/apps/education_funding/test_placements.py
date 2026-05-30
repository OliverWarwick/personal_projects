"""Tests for the per-fund placement-roster lookup."""

from __future__ import annotations

from personal_project.apps.education_funding.web.funds import get_fund_by_slug
from personal_project.apps.education_funding.web.placements import (
    get_placements_for_fund,
)


class TestPlacementsForFund:
    """Tests for ``get_placements_for_fund``."""

    def test_seeded_fund_returns_expected_count(self) -> None:
        """The seeded fund's placement count matches its ``placements_made``."""
        fund = get_fund_by_slug("pilot-training-tranche-i")
        assert fund is not None

        placements = get_placements_for_fund(fund.slug)

        assert len(placements) == fund.placements_made

    def test_unknown_fund_returns_empty_list(self) -> None:
        """An unknown fund slug returns an empty roster rather than raising."""
        assert get_placements_for_fund("does-not-exist") == []

    def test_each_placement_resolves_to_a_real_cadet(self) -> None:
        """Every placement entry resolves to a non-trivial cadet profile."""
        placements = get_placements_for_fund("pilot-training-tranche-i")

        assert all(p.cadet.name for p in placements)
        assert all(p.funded_amount_gbp > 0 for p in placements)
        assert all(p.stage for p in placements)
