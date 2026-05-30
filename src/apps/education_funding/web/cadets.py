"""Cadet profile data for the Peer-to-Peer investment view.

Holds the canonical list of pre-training cadet applicants surfaced on the
P2P marketplace. The biographical fields (name, age, location, archetype,
background) are taken directly from the example CVs in
``example_docs/pilot_cadet_applicant_cvs.md``.

The synthetic fields — airline programme and funding sought — are seeded
by the cadet's name so they remain stable across requests, refreshes, and
test runs. That means the displayed values won't reshuffle every time a
visitor lands on the page, while still feeling varied across cadets.
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass

# Funding amounts step in £1k increments — small enough to feel realistic,
# large enough that the row stays readable.
_FUNDING_INCREMENT_GBP = 1_000

# Funding-sought target falls between 50% and 100% of training cost.
_MIN_FUNDING_FRACTION = 0.50
_MAX_FUNDING_FRACTION = 1.00

# Stable, name-hashed avatar palette. Tuned to sit comfortably alongside
# the warm-cream surface and coral accent without competing for attention.
_AVATAR_PALETTE: tuple[str, ...] = (
    "#C96342",  # coral (matches accent)
    "#7A8B6F",  # sage
    "#5B7BA3",  # blue-grey
    "#A66B8C",  # plum
    "#B58A3F",  # ochre
)


@dataclass(frozen=True)
class AirlineProgramme:
    """An airline-sponsored cadet programme a cadet is applying to.

    Attributes:
        key: Short stable identifier (``"easyjet"`` / ``"ryanair"``).
        display_name: Human-friendly programme name shown in the UI.
        training_cost_gbp: Headline cost the cadet needs funded for the
            programme.

    """

    key: str
    display_name: str
    training_cost_gbp: int


_AIRLINE_PROGRAMMES: tuple[AirlineProgramme, ...] = (
    AirlineProgramme(
        key="easyjet",
        display_name="easyJet (CAE)",
        training_cost_gbp=90_000,
    ),
    AirlineProgramme(
        key="ryanair",
        display_name="Ryanair Future Flyer",
        training_cost_gbp=120_000,
    ),
)


@dataclass(frozen=True)
class FundingBreakdown:
    """Three-way split of a cadet's training cost into funding buckets.

    Captures how the headline programme cost is divided across what
    investors have already committed (``secured``), what investors are
    still being asked to provide (``remaining``), and what the cadet has
    chosen to fund themselves (``self_funded``). The three buckets always
    sum to ``total_cost_gbp``.

    Attributes:
        total_cost_gbp: Headline cost of the cadet's training programme.
        sought_gbp: Amount the cadet is asking investors to fund.
        secured_gbp: Amount investors have already committed.

    """

    total_cost_gbp: int
    sought_gbp: int
    secured_gbp: int

    @property
    def remaining_gbp(self) -> int:
        """Amount still being sought from investors (sought minus secured)."""
        return self.sought_gbp - self.secured_gbp

    @property
    def self_funded_gbp(self) -> int:
        """Portion the cadet is covering themselves (cost minus sought)."""
        return self.total_cost_gbp - self.sought_gbp

    @property
    def secured_pct(self) -> float:
        """Secured amount as a percentage of total cost."""
        return 100.0 * self.secured_gbp / self.total_cost_gbp

    @property
    def remaining_pct(self) -> float:
        """Remaining amount as a percentage of total cost."""
        return 100.0 * self.remaining_gbp / self.total_cost_gbp

    @property
    def self_funded_pct(self) -> float:
        """Self-funded amount as a percentage of total cost."""
        return 100.0 * self.self_funded_gbp / self.total_cost_gbp


@dataclass(frozen=True)
class CadetProfile:
    """A cadet applicant on the peer-to-peer marketplace.

    Attributes:
        slug: URL-safe identifier (used by future detail-page routes).
        name: Cadet's full name as written on their CV.
        age: Age at the point of application.
        location: Home town or area.
        archetype: Short label summarising the applicant type, e.g.
            ``"A-Level school leaver"`` or ``"Career changer, age 28"``.
        background: One-line summary of the cadet's most relevant
            experience for an investor skim-read.
        airline_programme: The sponsored programme this cadet is
            applying to.
        funding_sought_gbp: Amount of funding the cadet is seeking from
            investors, in pounds. Always a multiple of
            ``_FUNDING_INCREMENT_GBP``.
        funding_secured_gbp: Amount investors have committed to date, in
            pounds. Always a multiple of ``_FUNDING_INCREMENT_GBP`` and
            never exceeds ``funding_sought_gbp``.

    """

    slug: str
    name: str
    age: int
    location: str
    archetype: str
    background: str
    airline_programme: AirlineProgramme
    funding_sought_gbp: int
    funding_secured_gbp: int

    @property
    def funding_pct_of_cost(self) -> int:
        """The cadet's funding ask as an integer percentage of training cost."""
        return round(
            100 * self.funding_sought_gbp / self.airline_programme.training_cost_gbp,
        )

    @property
    def funding_breakdown(self) -> FundingBreakdown:
        """Three-way split of training cost into secured / remaining / self-funded."""
        return FundingBreakdown(
            total_cost_gbp=self.airline_programme.training_cost_gbp,
            sought_gbp=self.funding_sought_gbp,
            secured_gbp=self.funding_secured_gbp,
        )

    @property
    def initials(self) -> str:
        """Return two-letter initials drawn from the cadet's name.

        Uses the first letter of the first name and the first letter of
        the surname — works for the seeded roster and degrades gracefully
        for single-word names.
        """
        parts = [p for p in self.name.split() if p]
        if not parts:
            return ""
        if len(parts) == 1:
            return parts[0][:1].upper()
        return (parts[0][:1] + parts[-1][:1]).upper()

    @property
    def avatar_color_hex(self) -> str:
        """Stable display colour for the cadet's avatar.

        Hashed from the cadet's name so the colour stays the same across
        every page render, but varies meaningfully between cadets.
        """
        digest = hashlib.sha256(self.name.encode("utf-8")).digest()
        return _AVATAR_PALETTE[digest[0] % len(_AVATAR_PALETTE)]


# Static base data drawn from example_docs/pilot_cadet_applicant_cvs.md.
# The synthetic fields (airline + funding) are layered on at build time.
_CADET_SEEDS: tuple[dict[str, str | int], ...] = (
    {
        "slug": "james-whitfield",
        "name": "James Whitfield",
        "age": 18,
        "location": "Leeds",
        "archetype": "A-Level school leaver",
        "background": (
            "Air Cadets corporal; gliding scholarships at RAF Syerston; "
            "trial lesson on Cessna 152."
        ),
    },
    {
        "slug": "priya-ramanathan",
        "name": "Priya Ramanathan",
        "age": 22,
        "location": "Bristol",
        "archetype": "Aerospace Engineering graduate",
        "background": (
            "BEng Aerospace Engineering 2:1; LAPL(A) with 40 hours; "
            "ex-Jet2 seasonal cabin crew."
        ),
    },
    {
        "slug": "daniel-osei",
        "name": "Daniel Osei",
        "age": 28,
        "location": "Croydon",
        "archetype": "Career changer, age 28",
        "background": (
            "FAA PPL with 47.5 hours; ATPL ground-school in progress; "
            "Eurostar customer-service supervisor."
        ),
    },
    {
        "slug": "sophie-hartley",
        "name": "Sophie Hartley",
        "age": 21,
        "location": "Edinburgh",
        "archetype": "First-class Physics graduate",
        "background": (
            "BSc Physics (1st); 24 hours powered flight instruction; "
            "four ATPL theory exams passed first time."
        ),
    },
    {
        "slug": "connor-mcallister",
        "name": "Connor McAllister",
        "age": 20,
        "location": "Portsmouth",
        "archetype": "Non-graduate, military-adjacent",
        "background": (
            "Glider pilot's licence; 14 hours powered instruction; "
            "Swissport airside operations at Southampton."
        ),
    },
)


def _build_cadet(seed: dict[str, str | int]) -> CadetProfile:
    """Layer the synthetic airline + funding fields onto a base seed.

    Uses a name-seeded ``Random`` instance so the result is stable across
    every call for a given cadet.
    """
    name = str(seed["name"])
    rng = random.Random(name)  # noqa: S311 - display data, not security

    programme = rng.choice(_AIRLINE_PROGRAMMES)
    cost = programme.training_cost_gbp
    min_units = int(cost * _MIN_FUNDING_FRACTION) // _FUNDING_INCREMENT_GBP
    max_units = int(cost * _MAX_FUNDING_FRACTION) // _FUNDING_INCREMENT_GBP
    funding_units = rng.randint(min_units, max_units)
    # Secured runs from 0% to 100% of the asked amount, in £1k steps —
    # gives a varied set of donut charts without ever exceeding the ask.
    secured_units = rng.randint(0, funding_units)

    return CadetProfile(
        slug=str(seed["slug"]),
        name=name,
        age=int(seed["age"]),
        location=str(seed["location"]),
        archetype=str(seed["archetype"]),
        background=str(seed["background"]),
        airline_programme=programme,
        funding_sought_gbp=funding_units * _FUNDING_INCREMENT_GBP,
        funding_secured_gbp=secured_units * _FUNDING_INCREMENT_GBP,
    )


def get_cadet_profiles() -> list[CadetProfile]:
    """Return the canonical list of cadets shown on the P2P marketplace.

    Returns:
        A list of ``CadetProfile`` instances in display order. The list
        and each profile's synthetic fields are deterministic — calling
        this function repeatedly returns equal data.

    """
    return [_build_cadet(seed) for seed in _CADET_SEEDS]
