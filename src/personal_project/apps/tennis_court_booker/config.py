"""Configuration loader for the tennis court booker application.

Reads ``tennis_court_booker.yaml`` from the project-level ``config/``
directory and exposes the parsed values as plain Python objects.  The config
file is the source of truth for default venues, valid start times, and any
other static settings that should be easy to change without touching code.

The ``venues`` section is organised by client type (``clubspark``,
``better_com``) and each entry carries a display name plus the slug(s) needed
by that client.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import yaml

from personal_project.apps.tennis_court_booker.models import VenueConfig

# Path to the shared config directory, resolved relative to this file so it
# works correctly both from the source tree and when the package is installed.
_CONFIG_DIR: Path = Path(__file__).parent.parent.parent / "config"
_VENUES_CONFIG_PATH: Path = _CONFIG_DIR / "tennis_court_booker.yaml"


def load_config(path: Path = _VENUES_CONFIG_PATH) -> dict[str, Any]:
    """Load and parse the tennis court booker YAML configuration file.

    Args:
        path: Path to the YAML config file.  Defaults to
            ``src/personal_project/config/tennis_court_booker.yaml``.

    Returns:
        The parsed configuration as a plain dictionary.

    Raises:
        FileNotFoundError: If *path* does not exist.
        ValueError: If the YAML content is malformed or the expected
            ``tennis_court_booker`` top-level key is absent.

    """
    if not path.exists():
        msg = f"Tennis court booker config file not found: {path}"
        raise FileNotFoundError(msg)

    raw: dict[str, Any] = cast(
        "dict[str, Any]",
        yaml.safe_load(path.read_text(encoding="utf-8")),
    )

    if "tennis_court_booker" not in raw:
        msg = f"Config file {path} must contain a top-level 'tennis_court_booker' key."
        raise ValueError(msg)

    return raw


def _parse_venue_entries(client: str, entries: list[Any]) -> list[VenueConfig]:
    """Parse a list of raw YAML venue entries for one client type.

    Each entry in *entries* is a single-key dict whose key is the display name
    (e.g. ``"burgess_park"``) and whose value is a list of attribute dicts
    (e.g. ``[{"venue": "BurgessParkSouthwark"}]``).  The attribute list is
    flattened into a single mapping before extracting ``venue`` and the
    optional ``activity``.

    Args:
        client: The client-type string, e.g. ``"clubspark"`` or
            ``"better_com"``.
        entries: The raw list of venue entries as parsed from YAML.

    Returns:
        A list of :class:`~personal_project.apps.tennis_court_booker.models.VenueConfig`
        instances, one per entry.

    Raises:
        ValueError: If an entry is missing the required ``venue`` attribute.

    """
    result: list[VenueConfig] = []
    for item in entries:
        item_dict: dict[str, Any] = cast("dict[str, Any]", item)
        for display_name, attr_list_raw in item_dict.items():
            attr_list: list[Any] = cast("list[Any]", attr_list_raw or [])
            attrs: dict[str, str] = {
                k: v
                for attr_dict in attr_list
                for k, v in cast("dict[str, str]", attr_dict).items()
            }
            venue_slug = attrs.get("venue")
            if not venue_slug:
                msg = (
                    f"Venue entry '{display_name}' under client '{client}' "
                    "is missing the required 'venue' attribute."
                )
                raise ValueError(msg)
            result.append(
                VenueConfig(
                    client=client,
                    venue=venue_slug,
                    activity=attrs.get("activity"),
                    display_name=display_name,
                )
            )
    return result


def get_default_venue_configs(path: Path = _VENUES_CONFIG_PATH) -> list[VenueConfig]:
    """Return all configured venues as structured :class:`VenueConfig` objects.

    Parses the ``tennis_court_booker.venues`` section of the YAML config,
    which is keyed by client type (``clubspark``, ``better_com``).  Each
    client section contains a list of named venue entries, each carrying the
    slug(s) required to query that client.

    Args:
        path: Path to the YAML config file.  Defaults to the bundled
            ``tennis_court_booker.yaml``.

    Returns:
        An ordered list of :class:`VenueConfig` instances across all client
        types, preserving the order defined in the config file.

    Raises:
        FileNotFoundError: If *path* does not exist.
        ValueError: If the config is malformed, the ``venues`` section is
            absent or empty, or any entry is missing a required ``venue``
            attribute.

    """
    config = load_config(path)
    section: dict[str, Any] = cast("dict[str, Any]", config.get("tennis_court_booker", {}))
    venues_raw: dict[str, Any] = cast("dict[str, Any]", section.get("venues") or {})

    if not venues_raw:
        msg = (
            f"Config file {path} must define a non-empty 'tennis_court_booker.venues' mapping."
        )
        raise ValueError(msg)

    result: list[VenueConfig] = []
    for client_key, entries_raw in venues_raw.items():
        entries: list[Any] = cast("list[Any]", entries_raw or [])
        result.extend(_parse_venue_entries(client_key, entries))

    if not result:
        msg = (
            f"Config file {path} must define a non-empty 'tennis_court_booker.venues' mapping."
        )
        raise ValueError(msg)

    return result


def get_valid_court_start_times(path: Path = _VENUES_CONFIG_PATH) -> list[int]:
    """Return the configured list of valid court start-time hours.

    These hours (in 24-hour format, 0-23) represent the time slots that are
    considered relevant for availability queries.  Slots starting at other
    hours are filtered out unless explicitly overridden by the caller.

    Args:
        path: Path to the YAML config file.  Defaults to the bundled
            ``tennis_court_booker.yaml``.

    Returns:
        An ordered list of integer hours, e.g. ``[17, 18, 19, 20, 21]``.

    Raises:
        FileNotFoundError: If *path* does not exist.
        ValueError: If the config is malformed or no ``valid_court_start_times``
            list is defined under ``tennis_court_booker``.

    """
    config = load_config(path)
    section: dict[str, Any] = cast("dict[str, Any]", config.get("tennis_court_booker", {}))
    hours: list[int] = cast("list[int]", section.get("valid_court_start_times", []))

    if not hours:
        msg = (
            f"Config file {path} must define a non-empty "
            "'tennis_court_booker.valid_court_start_times' list."
        )
        raise ValueError(msg)

    return hours
