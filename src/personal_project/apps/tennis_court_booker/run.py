"""Run script for the tennis court booker application.

Provides two sub-commands:

``availability``
    Print a formatted availability summary for one or more ClubSpark venues on
    a given date.  Results are filtered to ``valid_court_start_times`` from the
    YAML config by default; pass ``--hours`` to override.

``check``
    Check whether a specific court and time slot is still available at a single
    venue on a given date.

Usage examples::

    uv run tennis-court-booker availability --date 2026-03-10
    uv run tennis-court-booker availability --date 2026-03-10 --venues BurgessParkSouthwark SouthwarkPark
    uv run tennis-court-booker availability --date 2026-03-10 --hours 17,18,19
    uv run tennis-court-booker check --date 2026-03-10 --venue BurgessParkSouthwark --court "Court 1" --time 18:00

Entry point: ``tennis-court-booker`` (registered in ``pyproject.toml``).
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import sys
from typing import TYPE_CHECKING

import pandas as pd

from personal_project.apps.tennis_court_booker.config import (
    get_default_venue_configs,
    get_valid_court_start_times,
)
from personal_project.apps.tennis_court_booker.models import VenueConfig
from personal_project.apps.tennis_court_booker.service import (
    check_slot_availability,
    get_venue_availability,
    get_venue_availability_better,
)

if TYPE_CHECKING:
    from personal_project.apps.tennis_court_booker.models import VenueAvailability


# ---------------------------------------------------------------------------
# Output formatting helpers
# ---------------------------------------------------------------------------


def _build_availability_frame(
    results: list[tuple[str, VenueAvailability]],
    *,
    drop_fully_booked: bool = True,
) -> pd.DataFrame:
    """Build a DataFrame of court availability from a list of venue results.

    Constructs a DataFrame whose columns form a two-level MultiIndex of
    ``(venue, court)`` and whose index contains the slot start times (formatted
    as ``HH:MM`` strings).  Cell values are the strings ``"available"`` or
    ``"booked"``; cells where no slot exists at that time for that court are
    ``NaN``.

    Args:
        results: Pairs of ``(venue_slug, VenueAvailability)`` to include in
            the frame.  Column order follows the order of venues and courts as
            they appear in *results*.
        drop_fully_booked: When ``True`` (the default), drop any
            ``(venue, court)`` column where every slot for the day is
            ``"booked"`` — i.e. keep only courts that have at least one
            available slot.

    Returns:
        A :class:`pandas.DataFrame` with a ``MultiIndex`` column and a
        time-string index.  Returns an empty :class:`pandas.DataFrame` when
        *results* contains no slots.

    """
    # Preserve (venue, court) column ordering from the input sequence.
    seen_cols: dict[tuple[str, str], None] = {}
    for venue, availability in results:
        for court in availability.courts:
            seen_cols[(venue, court)] = None
    columns = list(seen_cols)

    all_times: set[str] = set()
    for _, availability in results:
        for slot in availability.slots:
            all_times.add(slot.start_time.strftime("%H:%M"))

    if not columns or not all_times:
        return pd.DataFrame()

    data: dict[tuple[str, str], dict[str, str]] = {col: {} for col in columns}
    for venue, availability in results:
        for slot in availability.slots:
            time_key = slot.start_time.strftime("%H:%M")
            data[(venue, slot.court_name)][time_key] = (
                "available" if slot.is_available else "booked"
            )

    df = pd.DataFrame(data, index=pd.Index(sorted(all_times), name="time"))
    df.columns = pd.MultiIndex.from_tuples(columns, names=["venue", "court"])

    if drop_fully_booked:
        mask = (df == "available").any(axis=0)
        df = df.loc[:, mask]

    return df


def _print_availability(results: list[tuple[str, VenueAvailability]]) -> None:
    """Print a formatted availability table for one or more venues.

    Delegates to :func:`_build_availability_frame` to compile a
    ``(venue, court)`` MultiIndex DataFrame, then prints it to stdout.
    Courts that are fully booked for every slot of the day are excluded from
    the output by default.

    Args:
        results: Pairs of ``(venue_slug, VenueAvailability)`` to display.

    """
    date: datetime.date | None = next(
        (slot.date for _, avail in results for slot in avail.slots),
        None,
    )
    if date is not None:
        print(f"Date: {date}")
        print()

    frame = _build_availability_frame(results)

    if frame.empty:
        print("  No slots found.")
        print()
        return

    print(frame.ffill().to_string())
    print()


def _print_check_result(venue: str, court: str, time: datetime.time, *, available: bool) -> None:
    """Print a single check-result line.

    Args:
        venue: The ClubSpark venue slug.
        court: The court name that was checked.
        time: The time slot that was checked.
        available: Whether the slot is available.

    """
    status = "AVAILABLE" if available else "not available"
    print(f"  {venue:<40}  {court} at {time.strftime('%H:%M')}  ->  {status}")


# ---------------------------------------------------------------------------
# Async runners (one per sub-command)
# ---------------------------------------------------------------------------


async def _run_availability(
    venues: list[VenueConfig],
    date: datetime.date,
    hours: list[int],
) -> None:
    """Fetch and print filtered availability for all *venues* on *date* concurrently.

    Dispatches ClubSpark venues to :func:`~service.get_venue_availability` and
    Better.com venues to :func:`~service.get_venue_availability_better`.
    Multiple activities configured for the same Better.com venue are fetched
    as separate concurrent tasks.  All tasks run concurrently via
    :func:`asyncio.gather`.

    Args:
        venues: List of :class:`~models.VenueConfig` objects to query.
        date: The date to check.
        hours: Start-time hours to include in the results.

    """
    tasks: list[asyncio.Task[VenueAvailability]] = []
    labels: list[str] = []

    for vc in venues:
        label = vc.display_name or vc.venue
        if vc.client == "clubspark":
            tasks.append(asyncio.ensure_future(
                get_venue_availability(vc.venue, date, hours=hours)
            ))
            labels.append(label)
        elif vc.client == "better_com" and vc.activity:
            tasks.append(asyncio.ensure_future(
                get_venue_availability_better(vc.venue, vc.activity, date, hours=hours)
            ))
            labels.append(label)

    raw_results = await asyncio.gather(*tasks, return_exceptions=True)

    header = f"Court availability for {date}  (hours: {', '.join(str(h) for h in hours)})"
    print(header)
    print("=" * len(header))
    print()

    successful: list[tuple[str, VenueAvailability]] = []
    for label, result in zip(labels, raw_results, strict=True):
        if isinstance(result, BaseException):
            print(f"  {label}: ERROR - {result}")
            print()
        else:
            successful.append((label, result))

    if successful:
        _print_availability(successful)


async def _run_check(
    venue: str,
    date: datetime.date,
    court: str,
    time: datetime.time,
) -> None:
    """Check a specific slot at *venue* and print the result.

    Args:
        venue: ClubSpark venue slug to query.
        date: The date to check.
        court: Exact court name to look for (e.g. ``"Court 1"``).
        time: Start time of the slot to verify.

    """
    header = f"Slot check: {court} at {time.strftime('%H:%M')} on {date}"
    print(header)
    print("=" * len(header))
    print()

    try:
        available = await check_slot_availability(venue, date, court, time)
        _print_check_result(venue, court, time, available=available)
    except Exception as exc:
        print(f"  {venue:<40}  ERROR - {exc}")

    print()


# ---------------------------------------------------------------------------
# Argument parsing helpers
# ---------------------------------------------------------------------------


def _parse_time_arg(value: str) -> datetime.time:
    """Parse a ``HH:MM`` string into a :class:`datetime.time` for argparse.

    Args:
        value: The raw string supplied on the command line.

    Returns:
        A :class:`datetime.time` representing the slot start time.

    Raises:
        argparse.ArgumentTypeError: If *value* is not a valid ``HH:MM`` time.

    """
    try:
        return datetime.time.fromisoformat(value)
    except ValueError:
        msg = f"Invalid time '{value}'. Use HH:MM format, e.g. 18:00."
        raise argparse.ArgumentTypeError(msg) from None


def _parse_hours_arg(value: str) -> list[int]:
    """Parse a comma-separated hours string into a list of integers for argparse.

    Args:
        value: Comma-separated 24-hour integers, e.g. ``"17,18,19"``.

    Returns:
        A list of integer hour values, e.g. ``[17, 18, 19]``.

    Raises:
        argparse.ArgumentTypeError: If *value* cannot be parsed as a
            comma-separated list of integers in the range 0-23.

    """
    try:
        hours = [int(h.strip()) for h in value.split(",") if h.strip()]
    except ValueError:
        msg = f"Invalid hours '{value}'. Use comma-separated integers, e.g. 17,18,19."
        raise argparse.ArgumentTypeError(msg) from None

    invalid = [h for h in hours if not 0 <= h <= 23]  # noqa: PLR2004
    if invalid:
        msg = f"Hours out of range (0-23): {invalid}"
        raise argparse.ArgumentTypeError(msg)

    return hours


def _build_parser() -> argparse.ArgumentParser:
    """Build and return the top-level argument parser.

    Returns:
        A configured :class:`argparse.ArgumentParser` with ``availability``
        and ``check`` sub-commands registered.

    """
    parser = argparse.ArgumentParser(
        prog="tennis-court-booker",
        description="Check ClubSpark tennis court availability.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # -- availability sub-command ------------------------------------------
    avail = subparsers.add_parser(
        "availability",
        help="Show available slots for one or more venues on a given date.",
    )
    avail.add_argument(
        "--date",
        required=True,
        type=datetime.date.fromisoformat,
        metavar="YYYY-MM-DD",
        help="Date to check (ISO format, e.g. 2026-03-10).",
    )
    avail.add_argument(
        "--venues",
        nargs="+",
        default=None,
        metavar="VENUE",
        help=(
            "One or more ClubSpark venue slugs.  Defaults to the list in "
            "config/tennis_court_booker.yaml."
        ),
    )
    avail.add_argument(
        "--hours",
        default=None,
        type=_parse_hours_arg,
        metavar="H,H,...",
        help=(
            "Comma-separated 24-hour start times to show, e.g. 17,18,19.  "
            "Defaults to valid_court_start_times from config/tennis_court_booker.yaml."
        ),
    )

    # -- check sub-command --------------------------------------------------
    check = subparsers.add_parser(
        "check",
        help="Check whether a specific court slot is available at a single venue.",
    )
    check.add_argument(
        "--date",
        required=True,
        type=datetime.date.fromisoformat,
        metavar="YYYY-MM-DD",
        help="Date to check (ISO format, e.g. 2026-03-10).",
    )
    check.add_argument(
        "--venue",
        required=True,
        metavar="VENUE",
        help="ClubSpark venue slug, e.g. BurgessParkSouthwark.",
    )
    check.add_argument(
        "--court",
        required=True,
        metavar="NAME",
        help='Exact court name as shown in the booking grid, e.g. "Court 1".',
    )
    check.add_argument(
        "--time",
        required=True,
        type=_parse_time_arg,
        metavar="HH:MM",
        help="Start time of the slot to verify (24-hour format, e.g. 18:00).",
    )

    return parser


def _resolve_venues(cli_venues: list[str] | None) -> list[VenueConfig]:
    """Return venue configs from CLI args or the YAML config.

    When *cli_venues* is provided, each slug is wrapped as a ClubSpark
    :class:`~models.VenueConfig` (CLI-supplied slugs are assumed to be
    ClubSpark venues).  Otherwise all venues are loaded from the YAML config
    via :func:`~config.get_default_venue_configs`, which includes venues from
    all configured clients.

    Args:
        cli_venues: The list passed via ``--venues``, or ``None`` when the
            flag was omitted.

    Returns:
        A non-empty list of :class:`~models.VenueConfig` objects.

    Raises:
        SystemExit: If no venues can be resolved (config missing or empty).

    """
    if cli_venues:
        return [VenueConfig(client="clubspark", venue=v) for v in cli_venues]
    try:
        return get_default_venue_configs()
    except (FileNotFoundError, ValueError) as exc:
        print(f"Error loading venues from config: {exc}", file=sys.stderr)
        sys.exit(1)


def _resolve_hours(cli_hours: list[int] | None) -> list[int]:
    """Return *cli_hours* if provided, otherwise load defaults from config.

    Args:
        cli_hours: Hours already parsed from ``--hours``, or ``None`` when
            the flag was omitted.

    Returns:
        A non-empty list of integer hours.

    Raises:
        SystemExit: If no hours can be resolved (config missing or empty).

    """
    if cli_hours is not None:
        return cli_hours
    try:
        return get_valid_court_start_times()
    except (FileNotFoundError, ValueError) as exc:
        print(f"Error loading valid_court_start_times from config: {exc}", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Parse arguments and dispatch to the appropriate sub-command.

    This is the registered console-script entry point
    (``tennis-court-booker``).
    """
    parser = _build_parser()
    args = parser.parse_args()

    if args.command == "availability":
        venues = _resolve_venues(args.venues)
        hours = _resolve_hours(args.hours)
        asyncio.run(_run_availability(venues, args.date, hours))
    else:
        asyncio.run(_run_check(args.venue, args.date, args.court, args.time))


if __name__ == "__main__":
    main()
