"""Tests for the tennis court booker configuration loader.

Verifies that :func:`load_config` and :func:`get_default_venue_configs`
correctly parse the structured multi-client YAML config and raise appropriate
errors for missing or malformed input.  All tests use a temporary directory so
no real config file is touched (except the final integration-style test that
loads the bundled config).
"""

from __future__ import annotations

from pathlib import Path  # noqa: TC003

import pytest

from personal_project.apps.tennis_court_booker.config import (
    get_default_venue_configs,
    load_config,
)
from personal_project.apps.tennis_court_booker.models import VenueConfig

# ---------------------------------------------------------------------------
# YAML fixtures
# ---------------------------------------------------------------------------

_VALID_YAML = """\
tennis_court_booker:
  venues:
    clubspark:
      - burgess_park:
        - venue: BurgessParkSouthwark
      - southwark_park:
        - venue: SouthwarkPark
    better_com:
      - islington_indoor:
        - venue: islington-tennis-centre
        - activity: tennis-court-indoor
  valid_court_start_times:
    - 17
    - 18
"""

_CLUBSPARK_ONLY_YAML = """\
tennis_court_booker:
  venues:
    clubspark:
      - burgess_park:
        - venue: BurgessParkSouthwark
  valid_court_start_times:
    - 17
"""

_MISSING_KEY_YAML = """\
some_other_section:
  foo: bar
"""

_EMPTY_VENUES_YAML = """\
tennis_court_booker:
  venues: {}
"""


def _write(tmp_path: Path, content: str, name: str = "config.yaml") -> Path:
    """Write *content* to a file under *tmp_path* and return the path.

    Args:
        tmp_path: pytest-provided temporary directory.
        content: YAML content to write.
        name: File name within *tmp_path*.

    Returns:
        The :class:`Path` of the created file.

    """
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# load_config tests
# ---------------------------------------------------------------------------


class TestLoadConfig:
    """Tests for load_config."""

    def test_returns_dict_for_valid_yaml(self, tmp_path: Path) -> None:
        """Return a dictionary when the YAML file is well-formed."""
        p = _write(tmp_path, _VALID_YAML)
        result = load_config(p)
        assert isinstance(result, dict)

    def test_includes_tennis_court_booker_key(self, tmp_path: Path) -> None:
        """Return a dict that contains the tennis_court_booker top-level key."""
        p = _write(tmp_path, _VALID_YAML)
        result = load_config(p)
        assert "tennis_court_booker" in result

    def test_raises_file_not_found_for_missing_file(self, tmp_path: Path) -> None:
        """Raise FileNotFoundError when the config file does not exist."""
        missing = tmp_path / "nonexistent.yaml"
        with pytest.raises(FileNotFoundError, match="not found"):
            load_config(missing)

    def test_raises_value_error_for_missing_key(self, tmp_path: Path) -> None:
        """Raise ValueError when the tennis_court_booker key is absent."""
        p = _write(tmp_path, _MISSING_KEY_YAML)
        with pytest.raises(ValueError, match="tennis_court_booker"):
            load_config(p)


# ---------------------------------------------------------------------------
# get_default_venue_configs tests
# ---------------------------------------------------------------------------


class TestGetDefaultVenueConfigs:
    """Tests for get_default_venue_configs."""

    def test_returns_list_of_venue_configs(self, tmp_path: Path) -> None:
        """Return a list of VenueConfig instances for a valid config."""
        p = _write(tmp_path, _VALID_YAML)
        result = get_default_venue_configs(p)
        assert isinstance(result, list)
        assert all(isinstance(v, VenueConfig) for v in result)

    def test_clubspark_venues_have_correct_client(self, tmp_path: Path) -> None:
        """Set client='clubspark' on ClubSpark venue entries."""
        p = _write(tmp_path, _VALID_YAML)
        configs = get_default_venue_configs(p)
        cs = [v for v in configs if v.client == "clubspark"]
        assert len(cs) == 2  # noqa: PLR2004

    def test_better_com_venues_have_correct_client(self, tmp_path: Path) -> None:
        """Set client='better_com' on Better.com venue entries."""
        p = _write(tmp_path, _VALID_YAML)
        configs = get_default_venue_configs(p)
        bc = [v for v in configs if v.client == "better_com"]
        assert len(bc) == 1

    def test_better_com_venues_have_activity(self, tmp_path: Path) -> None:
        """Populate the activity field for Better.com entries."""
        p = _write(tmp_path, _VALID_YAML)
        configs = get_default_venue_configs(p)
        bc = next(v for v in configs if v.client == "better_com")
        assert bc.activity == "tennis-court-indoor"

    def test_clubspark_venues_have_no_activity(self, tmp_path: Path) -> None:
        """Leave activity as None for ClubSpark entries."""
        p = _write(tmp_path, _CLUBSPARK_ONLY_YAML)
        configs = get_default_venue_configs(p)
        assert all(v.activity is None for v in configs)

    def test_venue_slug_is_correct(self, tmp_path: Path) -> None:
        """Extract the correct venue slug from each entry."""
        p = _write(tmp_path, _CLUBSPARK_ONLY_YAML)
        configs = get_default_venue_configs(p)
        assert configs[0].venue == "BurgessParkSouthwark"

    def test_display_name_is_set(self, tmp_path: Path) -> None:
        """Populate display_name from the YAML entry key."""
        p = _write(tmp_path, _CLUBSPARK_ONLY_YAML)
        configs = get_default_venue_configs(p)
        assert configs[0].display_name == "burgess_park"

    def test_raises_file_not_found_for_missing_file(self, tmp_path: Path) -> None:
        """Propagate FileNotFoundError from load_config."""
        missing = tmp_path / "nonexistent.yaml"
        with pytest.raises(FileNotFoundError):
            get_default_venue_configs(missing)

    def test_raises_value_error_for_empty_venues(self, tmp_path: Path) -> None:
        """Raise ValueError when the venues mapping is empty."""
        p = _write(tmp_path, _EMPTY_VENUES_YAML)
        with pytest.raises(ValueError, match="non-empty"):
            get_default_venue_configs(p)

    def test_raises_value_error_for_missing_venues_key(self, tmp_path: Path) -> None:
        """Raise ValueError when the venues key is absent from the section."""
        yaml_content = "tennis_court_booker:\n  other_key: value\n"
        p = _write(tmp_path, yaml_content)
        with pytest.raises(ValueError, match="non-empty"):
            get_default_venue_configs(p)

    def test_default_path_loads_real_config(self) -> None:
        """Load the bundled config and confirm at least one ClubSpark venue exists."""
        configs = get_default_venue_configs()
        cs = [v for v in configs if v.client == "clubspark"]
        assert len(cs) >= 1
        slugs = [v.venue for v in cs]
        assert "BurgessParkSouthwark" in slugs
