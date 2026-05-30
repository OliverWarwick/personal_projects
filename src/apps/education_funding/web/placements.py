"""Placed-cadet data for the per-fund placements roster.

Surfaces the cadets that a given fund has already backed. Each entry pairs
an underlying ``CadetProfile`` (drawn from the example CVs) with placement-
specific metadata: how much they were funded, what training stage they're
in, and when they were placed.

The MVP roster is hardcoded against the single seeded fund. When more
funds appear, slot their placement rosters into ``_PLACEMENTS_BY_FUND``.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.apps.education_funding.web.cadets import (
    CadetProfile,
    get_cadet_profiles,
)


@dataclass(frozen=True)
class PlacedCadet:
    """A cadet who has been backed by a fund.

    Attributes:
        cadet: The underlying applicant profile (name, age, background,
            programme, etc.) drawn from the canonical cadet roster.
        funded_amount_gbp: Capital actually placed against this cadet, in
            pounds. May differ from their original ``funding_sought_gbp``
            once terms were finalised.
        stage: Training stage the cadet is currently in (e.g.
            ``"Phase 1 - Ground school"``).
        placement_date: Human-friendly placement date, e.g. ``"March 2026"``.

    """

    cadet: CadetProfile
    funded_amount_gbp: int
    stage: str
    placement_date: str


# Per-fund placement metadata. Each entry resolves a cadet by slug against
# the canonical roster and layers placement-specific fields on top.
_PLACEMENTS_BY_FUND: dict[str, tuple[dict[str, str | int], ...]] = {
    "pilot-training-tranche-i": (
        {
            "slug": "sophie-hartley",
            "funded_amount_gbp": 81_000,
            "stage": "Phase 3 - Multi-engine IR",
            "placement_date": "October 2025",
        },
        {
            "slug": "priya-ramanathan",
            "funded_amount_gbp": 92_000,
            "stage": "Phase 2 - Single-engine flight training",
            "placement_date": "January 2026",
        },
        {
            "slug": "daniel-osei",
            "funded_amount_gbp": 99_000,
            "stage": "Phase 1 - Ground school",
            "placement_date": "February 2026",
        },
        {
            "slug": "james-whitfield",
            "funded_amount_gbp": 88_000,
            "stage": "Phase 1 - Ground school",
            "placement_date": "March 2026",
        },
    ),
}


def get_placements_for_fund(fund_slug: str) -> list[PlacedCadet]:
    """Return the placed-cadet roster for the supplied fund.

    Args:
        fund_slug: URL slug identifying the fund (matches ``Fund.slug``).

    Returns:
        A list of ``PlacedCadet`` instances in display order. Empty when
        the fund has no placements (or is not registered here yet).

    """
    placement_entries = _PLACEMENTS_BY_FUND.get(fund_slug, ())
    cadets_by_slug = {profile.slug: profile for profile in get_cadet_profiles()}

    placements: list[PlacedCadet] = []
    for entry in placement_entries:
        cadet = cadets_by_slug.get(str(entry["slug"]))
        if cadet is None:
            continue
        placements.append(
            PlacedCadet(
                cadet=cadet,
                funded_amount_gbp=int(entry["funded_amount_gbp"]),
                stage=str(entry["stage"]),
                placement_date=str(entry["placement_date"]),
            ),
        )
    return placements
