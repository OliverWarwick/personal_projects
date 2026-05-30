"""Tests for the financing-programme catalogue."""

from __future__ import annotations

from personal_project.apps.education_funding.web.programs import (
    get_program_by_slug,
    get_programs,
)


class TestPrograms:
    """Tests for ``get_programs`` and ``get_program_by_slug``."""

    def test_lists_commercial_flying_programme(self) -> None:
        """The MVP roster surfaces the single open programme."""
        programs = get_programs()

        assert len(programs) >= 1
        slugs = {p.slug for p in programs}
        assert "commercial-flying-program" in slugs

    def test_slug_uses_american_spelling(self) -> None:
        """URL slug uses ``program`` (American), not ``programme``."""
        programs = get_programs()

        for program in programs:
            assert "programme" not in program.slug

    def test_display_name_uses_british_spelling(self) -> None:
        """Display copy keeps the British ``Programme`` spelling."""
        program = get_program_by_slug("commercial-flying-program")

        assert program is not None
        assert program.display_name == "Commercial Flying Programme"

    def test_unknown_slug_returns_none(self) -> None:
        """An unregistered slug returns ``None`` rather than raising."""
        assert get_program_by_slug("does-not-exist") is None
