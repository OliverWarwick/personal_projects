"""Application data model for the client-side financing portal.

Holds the ``Application`` dataclass surfaced by the application portal
and the dashboard, plus the lifecycle status constants. Persistence and
file-upload handling live in ``application_store``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

ApplicationStatus = Literal["saved", "submitted", "pending_review", "live"]
"""Lifecycle stages an application can sit in.

* ``saved`` — draft, still being filled in.
* ``submitted`` — pushed for review.
* ``pending_review`` — actively being assessed.
* ``live`` — approved and funded.
"""

APPLICATION_STATUS_ORDER: tuple[ApplicationStatus, ...] = (
    "saved",
    "submitted",
    "pending_review",
    "live",
)
"""Display order for the four-step status stepper."""

APPLICATION_STATUS_LABELS: dict[ApplicationStatus, str] = {
    "saved": "Saved",
    "submitted": "Submitted",
    "pending_review": "Pending review",
    "live": "Live",
}
"""Friendly labels for each status."""


@dataclass
class Application:
    """An in-flight or submitted financing application owned by a single client.

    Attributes:
        program_slug: Slug of the financing programme this application
            sits under (e.g. ``"commercial-flying-program"``).
        reference: Human-friendly application reference, e.g. ``"APP-3F0E"``.
        status: Current lifecycle stage, one of ``ApplicationStatus``.
        full_name: Candidate's full legal name.
        date_of_birth: ISO date string (``YYYY-MM-DD``).
        location: Home town / city, country.
        nationality: Nationality / citizenship.
        personal_statement: Short bio surfacing what the candidate is
            about beyond their CV.
        programme: Sponsored cadet programme they want to enrol on, free
            text (e.g. ``"easyJet MPL (CAE Oxford)"``).
        motivation: Free-text answer to "why this career, why now".
        aviation_experience: Any prior flying / simulator / cadet / ATC
            experience.
        work_experience: Relevant employment history.
        cv_filename: Stored filename of the uploaded CV (relative to the
            user's upload directory). ``None`` until uploaded.
        video_filename: Stored filename of the uploaded intro video.
            ``None`` until uploaded.
        video_url: External video URL (e.g. YouTube / Vimeo) used as an
            alternative to a file upload.
        created_at: ISO timestamp of first save.
        last_updated: ISO timestamp of the most recent save.
        submitted_at: ISO timestamp of submission, or ``None`` while
            still in draft.

    """

    program_slug: str
    reference: str
    status: ApplicationStatus = "saved"

    # About yourself
    full_name: str = ""
    date_of_birth: str = ""
    location: str = ""
    nationality: str = ""
    personal_statement: str = ""

    # Programme target
    programme: str = ""

    # Motivation
    motivation: str = ""

    # Experience
    aviation_experience: str = ""
    work_experience: str = ""

    # Funding ask. Stored as integer pounds. ``funding_gap_gbp`` is
    # derived (cost minus requested) and surfaces on the portal as the
    # amount the candidate is committing to fund themselves.
    total_program_cost_gbp: int = 0
    funding_requested_gbp: int = 0
    self_funded_explanation: str = ""

    # Uploads
    cv_filename: str | None = None
    video_filename: str | None = None
    video_url: str | None = None
    proof_of_funds_filename: str | None = None

    # Metadata
    created_at: str = ""
    last_updated: str = ""
    submitted_at: str | None = None

    # Per-section completion flags. Stored explicitly so the candidate
    # can tick a section "done" by saving it, rather than relying on
    # heuristics over the field values. Defaults to all-False.
    sections_completed: dict[str, bool] = field(
        default_factory=lambda: {
            "about": False,
            "motivation": False,
            "experience": False,
            "funding": False,
            "cv": False,
            "video": False,
        },
    )

    @property
    def is_editable(self) -> bool:
        """Return ``True`` while the candidate can still edit the draft."""
        return self.status == "saved"

    @property
    def funding_gap_gbp(self) -> int:
        """Amount the candidate is covering themselves (cost minus requested).

        Clamped to zero if the candidate enters a request larger than
        the programme cost — the form shouldn't let that happen but the
        guard keeps templates robust.
        """
        gap = self.total_program_cost_gbp - self.funding_requested_gbp
        return max(0, gap)

    @property
    def all_sections_complete(self) -> bool:
        """Return ``True`` when every required section has been saved at least once."""
        required = ("about", "motivation", "experience", "funding", "cv")
        return all(self.sections_completed.get(s, False) for s in required)
