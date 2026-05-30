"""Tests for the CV markdown loader and renderer."""

from __future__ import annotations

from personal_project.apps.education_funding.web.cv_loader import get_cv_html


class TestGetCvHtml:
    """Tests for ``get_cv_html``."""

    def test_known_slug_returns_html_with_name(self) -> None:
        """A known slug returns HTML containing the cadet's name."""
        html = get_cv_html("james-whitfield")

        assert html is not None
        assert "James Whitfield" in html
        assert "<h3>" in html  # ``###`` headers became ``<h3>``

    def test_known_slug_includes_personal_statement(self) -> None:
        """The rendered CV contains the canonical Personal Statement section."""
        html = get_cv_html("priya-ramanathan")

        assert html is not None
        assert "Personal Statement" in html

    def test_unknown_slug_returns_none(self) -> None:
        """An unregistered slug returns ``None``."""
        assert get_cv_html("does-not-exist") is None

    def test_section_does_not_leak_into_next_cv(self) -> None:
        """One cadet's CV doesn't bleed into the next CV's content."""
        james = get_cv_html("james-whitfield")

        assert james is not None
        # Priya is the next cadet in the markdown — her name must not
        # appear inside James's section.
        assert "Priya Ramanathan" not in james
