"""Peer-to-peer batch data for the P2P investment view.

A batch groups a cohort of cadet candidates into a single investable unit,
analogous to a fund but with direct per-cadet visibility. Investors click into
a batch to see the objectives, return projections, and individual cadet cards,
then back specific candidates.

Data lives here in code to keep the prototype self-contained — wire into the
persistence layer once the shape stabilises.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class P2PBatch:
    """A cohort of cadet candidates grouped for peer-to-peer investment.

    Attributes:
        slug: URL-safe identifier used in batch-detail routes.
        name: Display name of the batch.
        status: Lifecycle label shown on the listing card (e.g. ``"Open"``).
        strategy: One-line strategy summary shown on the listing card.
        target_size_gbp: Aggregate funding target across all cadets in the
            batch, in pounds.
        raised_gbp: Total capital committed to cadets in this batch to date,
            in pounds.
        placements_target: Number of cadets in the batch.
        placements_made: Number of cadets whose funding is fully secured.
        objectives: Multi-line description of the batch's investment
            objectives.
        cadet_slugs: Ordered tuple of cadet slugs belonging to this batch.

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
    cadet_slugs: tuple[str, ...]

    @property
    def raised_pct(self) -> int:
        """Percentage of the target size that has been raised so far."""
        if self.target_size_gbp <= 0:
            return 0
        return round(100 * self.raised_gbp / self.target_size_gbp)


_BATCHES: tuple[P2PBatch, ...] = (
    P2PBatch(
        slug="commercial-pilot-training-batch-i",
        name="Pilot Cohort — Batch I",
        status="Open",
        strategy=(
            "Direct income-share backing for five pre-training cadet candidates "
            "across easyJet and Ryanair sponsored programmes."
        ),
        target_size_gbp=500_000,
        raised_gbp=180_000,
        placements_target=5,
        placements_made=2,
        objectives=(
            "Pilot Cohort Batch I offers investors direct exposure to individual "
            "cadet candidates at the pre-training stage. Each placement is "
            "structured as a bilateral Income Share Agreement between the investor "
            "and the funded pilot, giving investors full visibility of the "
            "candidate they are backing. Capital is deployed directly to the "
            "training provider on the cadet's behalf, and repayments flow back "
            "to the investor as a fixed percentage of the pilot's annual base "
            "salary for a period of up to twenty years, subject to an overall "
            "repayment cap. The batch targets candidates applying to airline-"
            "sponsored integrated ATPL programmes, where the conditional job "
            "offer reduces the career-entry risk relative to fully self-funded "
            "routes."
        ),
        cadet_slugs=(
            "james-whitfield",
            "priya-ramanathan",
            "daniel-osei",
            "sophie-hartley",
            "connor-mcallister",
        ),
    ),
)


def get_open_batches() -> list[P2PBatch]:
    """Return the list of P2P batches currently shown on the P2P tab.

    Returns:
        A list of open ``P2PBatch`` instances in display order.

    """
    return [b for b in _BATCHES if b.status == "Open"]


def get_batch_by_slug(slug: str) -> P2PBatch | None:
    """Look up a batch by its URL slug.

    Args:
        slug: The slug captured from the batch-detail URL.

    Returns:
        The matching ``P2PBatch`` instance, or ``None`` if no batch exists
        for the supplied slug.

    """
    for batch in _BATCHES:
        if batch.slug == slug:
            return batch
    return None
