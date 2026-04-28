"""Testimonial data for the client-side Testimonials page.

A small, hardcoded roster of made-up success stories from past clients
who raised funding through the programme. Dates fall within the last
four years so the stories feel current.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Testimonial:
    """A success story from a client who completed their training.

    Attributes:
        name: Client's full name.
        location: Home town or area at the time of training.
        programme: Sponsored programme they completed (e.g. easyJet MPL).
        year_completed: Year they reached the line — used for sorting and
            for the small date label on each testimonial card.
        quote: First-person quote describing the experience.
        outcome: Short summary of where they are now (e.g. their current
            airline and aircraft type).

    """

    name: str
    location: str
    programme: str
    year_completed: int
    quote: str
    outcome: str


_TESTIMONIALS: tuple[Testimonial, ...] = (
    Testimonial(
        name="Hannah Brooke",
        location="Manchester",
        programme="easyJet MPL (CAE Oxford)",
        year_completed=2025,
        quote=(
            "Without the programme I would have spent another two years saving "
            "and probably never started. Six months after my line check I was "
            "flying the A320 on European routes — it still doesn't feel real."
        ),
        outcome="Now First Officer, easyJet (Airbus A320), based at Manchester.",
    ),
    Testimonial(
        name="Marcus Chen",
        location="Glasgow",
        programme="Ryanair Future Flyer (Atlantic Flight Training Academy)",
        year_completed=2023,
        quote=(
            "I was 27 and walking away from a finance career. The programme "
            "took the financial fear out of the decision and let me focus on "
            "the training itself. The mentorship through the application "
            "process made a bigger difference than I expected."
        ),
        outcome="Now First Officer, Ryanair (Boeing 737-800), based at Stansted.",
    ),
    Testimonial(
        name="Aisha Okonkwo",
        location="Birmingham",
        programme="easyJet MPL (CAE Oxford)",
        year_completed=2022,
        quote=(
            "I'm the first person in my family to fly, never mind to qualify "
            "as a commercial pilot. Sponsored programmes only made sense to "
            "me on paper once the funding was in place — that's what unlocked "
            "the whole thing."
        ),
        outcome="Now First Officer, easyJet (Airbus A320), based at Luton.",
    ),
)


def get_testimonials() -> list[Testimonial]:
    """Return the testimonial roster in display order.

    Returns:
        A list of ``Testimonial`` instances, most recent first.

    """
    return sorted(_TESTIMONIALS, key=lambda t: t.year_completed, reverse=True)
