"""Financing-programme catalogue for the client side of the app.

Each ``Program`` represents a distinct financing product the client can
read about and apply to. The MVP ships a single open programme; new
programmes plug in by appending to ``_PROGRAMS``.

Note the spelling split:

* ``slug`` uses the American ``program`` (URL hygiene, wider web norm).
* ``display_name`` keeps the British ``Programme`` for UI copy.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Program:
    """A financing programme surfaced on the client dashboard.

    Attributes:
        slug: URL-safe identifier, used as the first path segment under
            ``/client/`` (e.g. ``"commercial-flying-program"``).
        display_name: Human-friendly programme name shown in the UI.
        tagline: One-line description shown alongside the programme name.
        status: Lifecycle label (e.g. ``"Open"`` / ``"Closed"``).

    """

    slug: str
    display_name: str
    tagline: str
    status: str


_PROGRAMS: tuple[Program, ...] = (
    Program(
        slug="commercial-flying-program",
        display_name="Commercial Flying Programme",
        tagline=(
            "Upfront training capital for UK cadets entering airline-sponsored "
            "commercial pilot training."
        ),
        status="Open",
    ),
)


def get_programs() -> list[Program]:
    """Return the active programme roster in display order.

    Returns:
        A list of ``Program`` instances. The MVP ships a single
        programme; new programmes plug in by extending ``_PROGRAMS``.

    """
    return list(_PROGRAMS)


def get_program_by_slug(slug: str) -> Program | None:
    """Look up a programme by its URL slug.

    Args:
        slug: The slug captured from the URL (e.g.
            ``"commercial-flying-program"``).

    Returns:
        The matching ``Program`` instance, or ``None`` when no programme
        exists for the supplied slug.

    """
    for program in _PROGRAMS:
        if program.slug == slug:
            return program
    return None
