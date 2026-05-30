"""Seeded portfolio positions for the investor portfolio page.

Holds a representative set of fund and peer-to-peer positions for the
prototype investor view. Data lives in code to keep the prototype
self-contained — wire into the persistence layer once the shape stabilises.

Each position captures the amount invested, the returns received so far,
and enough metadata to render the allocation chart and per-position cards.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FundPosition:
    """A fund investment held by the investor.

    Attributes:
        slug: Fund slug — matches the fund detail route.
        fund_name: Display name for the fund.
        invested_gbp: Capital committed to the fund, in pounds.
        returns_to_date_gbp: Cash returned to the investor so far, in pounds.
        vintage_year: Year the investment was made.
        color_hex: Chart slice colour for the allocation donut.

    """

    slug: str
    fund_name: str
    invested_gbp: int
    returns_to_date_gbp: int
    vintage_year: int
    color_hex: str


@dataclass(frozen=True)
class P2PPosition:
    """A peer-to-peer investment in an individual cadet.

    Attributes:
        cadet_slug: Cadet slug — matches the cadet detail route.
        cadet_name: Display name for the cadet.
        batch_slug: Batch slug — used to link back to the batch detail page.
        airline_programme: Name of the airline-sponsored programme.
        invested_gbp: Capital committed directly to this cadet, in pounds.
        returns_to_date_gbp: Cash received from repayments so far, in pounds.
        vintage_year: Year the investment was made.
        color_hex: Chart slice colour for the allocation donut.

    """

    cadet_slug: str
    cadet_name: str
    batch_slug: str
    airline_programme: str
    invested_gbp: int
    returns_to_date_gbp: int
    vintage_year: int
    color_hex: str


@dataclass(frozen=True)
class Portfolio:
    """Aggregate of all positions held by the investor.

    Provides convenience properties used by the template for summary
    widgets and the projected-returns timeline chart.

    Attributes:
        fund_positions: Ordered list of fund positions.
        p2p_positions: Ordered list of peer-to-peer cadet positions.

    """

    fund_positions: tuple[FundPosition, ...]
    p2p_positions: tuple[P2PPosition, ...]

    @property
    def total_invested_gbp(self) -> int:
        """Total capital deployed across all positions, in pounds."""
        return sum(p.invested_gbp for p in self.fund_positions) + sum(
            p.invested_gbp for p in self.p2p_positions
        )

    @property
    def total_returns_to_date_gbp(self) -> int:
        """Total cash returned so far across all positions, in pounds."""
        return sum(p.returns_to_date_gbp for p in self.fund_positions) + sum(
            p.returns_to_date_gbp for p in self.p2p_positions
        )

    @property
    def all_positions_for_chart(self) -> list[dict[str, object]]:
        """Return a flat list of label/value/color dicts for the donut chart.

        Fund positions appear first, then individual P2P cadets, so the
        chart legend reads in a natural grouped order.
        """
        rows: list[dict[str, object]] = [
            {"label": p.fund_name, "value": p.invested_gbp, "color": p.color_hex}
            for p in self.fund_positions
        ]
        rows.extend(
            {
                "label": f"P2P — {p.cadet_name}",
                "value": p.invested_gbp,
                "color": p.color_hex,
            }
            for p in self.p2p_positions
        )
        return rows


# ---------------------------------------------------------------------------
# Seeded portfolio — one fund position and three P2P cadet positions.
# ---------------------------------------------------------------------------

PORTFOLIO = Portfolio(
    fund_positions=(
        FundPosition(
            slug="commercial-pilot-fund-i",
            fund_name="Commercial Pilot Fund I",
            invested_gbp=50_000,
            returns_to_date_gbp=1_800,
            vintage_year=2024,
            color_hex="#C96342",  # coral — primary accent
        ),
    ),
    p2p_positions=(
        P2PPosition(
            cadet_slug="james-whitfield",
            cadet_name="James Whitfield",
            batch_slug="commercial-pilot-training-batch-i",
            airline_programme="easyJet (CAE)",
            invested_gbp=30_000,
            returns_to_date_gbp=0,
            vintage_year=2025,
            color_hex="#5B7BA3",  # blue-grey
        ),
        P2PPosition(
            cadet_slug="priya-ramanathan",
            cadet_name="Priya Ramanathan",
            batch_slug="commercial-pilot-training-batch-i",
            airline_programme="Ryanair Future Flyer",
            invested_gbp=25_000,
            returns_to_date_gbp=0,
            vintage_year=2025,
            color_hex="#7A8B6F",  # sage
        ),
        P2PPosition(
            cadet_slug="daniel-osei",
            cadet_name="Daniel Osei",
            batch_slug="commercial-pilot-training-batch-i",
            airline_programme="easyJet (CAE)",
            invested_gbp=20_000,
            returns_to_date_gbp=0,
            vintage_year=2025,
            color_hex="#B58A3F",  # ochre
        ),
    ),
)


def get_portfolio() -> Portfolio:
    """Return the seeded investor portfolio.

    Returns:
        The singleton ``Portfolio`` instance with all fund and P2P positions.

    """
    return PORTFOLIO
