"""Tests for the per-username application lookup."""

from __future__ import annotations

from personal_project.apps.education_funding.web.application_store import (
    get_application_for,
)
from personal_project.apps.education_funding.web.applications import (
    APPLICATION_STATUS_LABELS,
    APPLICATION_STATUS_ORDER,
    Application,
)

EXPECTED_STATUS_COUNT = 4


class TestApplicationStatuses:
    """Tests for the lifecycle constants."""

    def test_order_lists_all_four_statuses(self) -> None:
        """The status-order tuple lists each lifecycle stage once."""
        assert len(APPLICATION_STATUS_ORDER) == EXPECTED_STATUS_COUNT
        assert set(APPLICATION_STATUS_ORDER) == {
            "saved",
            "submitted",
            "pending_review",
            "live",
        }

    def test_every_status_has_a_friendly_label(self) -> None:
        """Each status code in ``APPLICATION_STATUS_ORDER`` has a label."""
        for status in APPLICATION_STATUS_ORDER:
            assert status in APPLICATION_STATUS_LABELS
            assert APPLICATION_STATUS_LABELS[status]


class TestGetApplicationFor:
    """Tests for the per-username application lookup."""

    def test_no_drafts_means_get_application_returns_none(self) -> None:
        """A user with no persisted drafts gets ``None``."""
        # Conftest isolates the data dir per test, so this user has no draft.
        assert get_application_for("ow6") is None

    def test_unknown_user_returns_none(self) -> None:
        """An unknown username returns ``None`` rather than raising."""
        assert get_application_for("nope") is None


class TestApplicationModel:
    """Smoke tests for the ``Application`` dataclass shape."""

    def test_application_is_constructible_with_minimal_fields(self) -> None:
        """``Application`` accepts the two required positional fields plus defaults."""
        app = Application(
            program_slug="commercial-flying-program",
            reference="APP-0001",
            status="submitted",
            programme="easyJet (CAE)",
            last_updated="27 April 2026",
        )

        assert app.reference == "APP-0001"
        assert app.status == "submitted"
        assert app.program_slug == "commercial-flying-program"
        assert app.is_editable is False  # submitted = locked

    def test_funding_gap_is_cost_minus_requested(self) -> None:
        """``funding_gap_gbp`` reports the self-funded portion of total cost."""
        app = Application(
            program_slug="commercial-flying-program",
            reference="APP-0002",
            total_program_cost_gbp=120_000,
            funding_requested_gbp=90_000,
        )

        assert app.funding_gap_gbp == 30_000  # noqa: PLR2004 - explicit £ math

    def test_funding_gap_clamps_to_zero_when_requested_exceeds_cost(self) -> None:
        """A request larger than the cost yields a zero gap, not a negative."""
        app = Application(
            program_slug="commercial-flying-program",
            reference="APP-0003",
            total_program_cost_gbp=100_000,
            funding_requested_gbp=120_000,
        )

        assert app.funding_gap_gbp == 0
