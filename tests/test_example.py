"""Example test to demonstrate TDD approach and ensure the test suite works."""

from personal_project import __version__


def test_version() -> None:
    """Test that version is defined."""
    assert isinstance(__version__, str)
    assert len(__version__) > 0
