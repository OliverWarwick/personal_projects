"""Tests for the fund-data lookup helpers used by the Funds tab."""

from __future__ import annotations

from personal_project.apps.education_funding.web.funds import (
    get_fund_by_slug,
    get_open_funds,
)

EXPECTED_OPEN_FUND_COUNT = 1


class TestOpenFunds:
    """Tests for the open-fund listing surfaced on the Funds tab."""

    def test_lists_pilot_training_tranche_one(self) -> None:
        """The MVP roster contains the single ``Pilot Training Tranche I`` fund."""
        funds = get_open_funds()

        assert len(funds) == EXPECTED_OPEN_FUND_COUNT
        assert funds[0].slug == "pilot-training-tranche-i"
        assert funds[0].name == "Pilot Training Tranche I"
        assert funds[0].status == "Open"


class TestFundLookup:
    """Tests for the slug-based fund lookup."""

    def test_known_slug_returns_fund(self) -> None:
        """A known slug returns the matching fund."""
        fund = get_fund_by_slug("pilot-training-tranche-i")

        assert fund is not None
        assert fund.name == "Pilot Training Tranche I"

    def test_unknown_slug_returns_none(self) -> None:
        """An unknown slug returns ``None`` rather than raising."""
        assert get_fund_by_slug("does-not-exist") is None

    def test_raised_pct_is_rounded_integer(self) -> None:
        """``raised_pct`` returns an integer percentage of target size."""
        fund = get_fund_by_slug("pilot-training-tranche-i")

        assert fund is not None
        assert isinstance(fund.raised_pct, int)
        assert 0 <= fund.raised_pct <= 100  # noqa: PLR2004 - 0/100 are domain bounds
