"""Fund data for the Investment Opportunities → Funds tab.

The MVP surfaces a single fund (``Pilot Training Tranche I``) with
placeholder objectives, sizing, and progress numbers. The data lives here
in code rather than in YAML/DB to keep the prototype self-contained — wire
this into the persistence layer once the shape stabilises.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Fund:
    """An open investment fund visible on the Funds tab.

    Attributes:
        slug: URL-safe identifier used in fund-detail routes.
        name: Display name of the fund.
        status: Lifecycle label shown alongside the fund (e.g. ``"Open"``).
        strategy: One-line strategy summary surfaced on the listing card.
        target_size_gbp: Target capital raise for the fund, in pounds.
        raised_gbp: Capital committed to date, in pounds.
        placements_target: Headline number of cadets the fund intends to
            place.
        placements_made: Number of cadets currently funded by the fund.
        objectives: Multi-line description of the fund's investment
            objectives — placeholder copy until filled in properly.
        description: Longer prose description of the fund. Placeholder.

    """

    slug: str
    name: str
    status: str
    strategy: str
    target_size_gbp: int
    raised_gbp: int
    placements_target: int
    placements_made: int
    objectives: str
    description: str

    @property
    def raised_pct(self) -> int:
        """Percentage of the target size that has been raised so far."""
        if self.target_size_gbp <= 0:
            return 0
        return round(100 * self.raised_gbp / self.target_size_gbp)


_FUNDS: tuple[Fund, ...] = (
    Fund(
        slug="pilot-training-tranche-i",
        name="Pilot Training Tranche I",
        status="Open",
        strategy=(
            "Income-share funding for UK cadets entering airline-sponsored "
            "training programmes."
        ),
        target_size_gbp=1_000_000,
        raised_gbp=620_000,
        placements_target=10,
        placements_made=4,
        objectives=(
            "Provide upfront training capital to candidates in the commercial "
            "pilot qualification in exchange for a fixed share of "
            "post-qualification earnings, capped and time-limited."
        ),
        description=(
            "Pilot Training Tranche I is the first investment vehicle in the "
            "platform's pilot-funding programme. Placeholder copy — full "
            "description, fee schedule, and investor terms to follow."
        ),
    ),
)


def get_open_funds() -> list[Fund]:
    """Return the list of funds currently shown on the Funds tab.

    Returns:
        A list of open ``Fund`` instances in display order.

    """
    return [fund for fund in _FUNDS if fund.status == "Open"]


def get_fund_by_slug(slug: str) -> Fund | None:
    """Look up a fund by its URL slug.

    Args:
        slug: The slug captured from the fund-detail URL.

    Returns:
        The matching ``Fund`` instance, or ``None`` if no fund exists for
        the supplied slug.

    """
    for fund in _FUNDS:
        if fund.slug == slug:
            return fund
    return None
