"""Tests for the tennis court booker run script.

Verifies argument parsing, output formatting helpers, and the venue/hours-resolution
logic.  All service calls are mocked so no browser or network is needed.
"""

from __future__ import annotations

import argparse
import datetime
from unittest.mock import AsyncMock, patch

import pandas as pd
import pytest

from personal_project.apps.tennis_court_booker.models import (
    BookingResult,
    CardDetails,
    CourtSlot,
    VenueAvailability,
    VenueConfig,
)
from personal_project.apps.tennis_court_booker.run import (
    _build_availability_frame,
    _build_parser,
    _parse_hours_arg,
    _parse_time_arg,
    _print_availability,
    _print_check_result,
    _resolve_card,
    _resolve_clubspark_credentials,
    _resolve_hours,
    _resolve_venues,
    _run_availability,
    _run_book,
    _run_book_clubspark,
    _run_card_credentials_delete,
    _run_card_credentials_set,
    _run_check,
    _run_clubspark_credentials_delete,
    _run_clubspark_credentials_set,
)

# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

DATE = datetime.date(2026, 3, 10)
TIME_09 = datetime.time(9, 0)
TIME_10 = datetime.time(10, 0)
TIME_11 = datetime.time(11, 0)
VENUE = "BurgessParkSouthwark"
VENUE_B = "TannerStPark"
VENUE_BETTER = "islington-tennis-centre"
ACTIVITY_BETTER = "tennis-court-indoor"
DEFAULT_HOURS = [17, 18, 19, 20, 21]
TWO_VENUES = 2

VENUE_CONFIG_CS = VenueConfig(client="clubspark", venue=VENUE)
VENUE_CONFIG_BETTER = VenueConfig(
    client="better_com", venue=VENUE_BETTER, activity=ACTIVITY_BETTER
)


def _make_availability(*, available: bool = True) -> VenueAvailability:
    """Build a minimal VenueAvailability with one slot for testing.

    Args:
        available: Whether the single test slot is available.

    Returns:
        A :class:`VenueAvailability` with one slot on Court 1.

    """
    slot = CourtSlot(
        court_name="Court 1",
        date=DATE,
        start_time=TIME_09,
        end_time=TIME_10,
        is_available=available,
    )
    return VenueAvailability(venue=VENUE, date=DATE, slots=[slot])


def _make_mixed_availability() -> VenueAvailability:
    """Build a VenueAvailability with one available and one booked slot on the same court.

    Used to exercise output paths where a court has mixed availability so it is
    not filtered out by the ``drop_fully_booked`` logic.

    Returns:
        A :class:`VenueAvailability` with two slots on Court 1: one available,
        one booked.

    """
    return VenueAvailability(
        venue=VENUE,
        date=DATE,
        slots=[
            CourtSlot("Court 1", DATE, TIME_09, TIME_10, is_available=True),
            CourtSlot("Court 1", DATE, TIME_10, TIME_11, is_available=False),
        ],
    )


# ---------------------------------------------------------------------------
# _parse_time_arg tests
# ---------------------------------------------------------------------------


class TestParseTimeArg:
    """Tests for the _parse_time_arg argparse converter."""

    def test_parses_valid_time(self) -> None:
        """Return a datetime.time for a valid HH:MM string."""
        assert _parse_time_arg("09:00") == datetime.time(9, 0)

    def test_parses_hhmm_with_minutes(self) -> None:
        """Accept times with a non-zero minute value."""
        assert _parse_time_arg("09:30") == datetime.time(9, 30)

    def test_raises_for_invalid_time(self) -> None:
        """Raise ArgumentTypeError for unrecognised time strings."""
        with pytest.raises(argparse.ArgumentTypeError, match="Invalid time"):
            _parse_time_arg("not-a-time")


# ---------------------------------------------------------------------------
# _parse_hours_arg tests
# ---------------------------------------------------------------------------


class TestParseHoursArg:
    """Tests for the _parse_hours_arg argparse converter."""

    def test_parses_multiple_hours(self) -> None:
        """Return a list of ints for a comma-separated hours string."""
        assert _parse_hours_arg("17,18,19") == [17, 18, 19]

    def test_parses_single_hour(self) -> None:
        """Return a single-element list for a lone integer."""
        assert _parse_hours_arg("18") == [18]

    def test_ignores_whitespace(self) -> None:
        """Strip whitespace around each hour value."""
        assert _parse_hours_arg("17, 18, 19") == [17, 18, 19]

    def test_raises_for_non_integer(self) -> None:
        """Raise ArgumentTypeError when the value cannot be parsed as integers."""
        with pytest.raises(argparse.ArgumentTypeError, match="Invalid hours"):
            _parse_hours_arg("morning,evening")

    def test_raises_for_hour_out_of_range(self) -> None:
        """Raise ArgumentTypeError when an hour is outside 0-23."""
        with pytest.raises(argparse.ArgumentTypeError, match="out of range"):
            _parse_hours_arg("17,25")

    def test_raises_for_negative_hour(self) -> None:
        """Raise ArgumentTypeError for negative hour values."""
        with pytest.raises(argparse.ArgumentTypeError, match="out of range"):
            _parse_hours_arg("-1,18")


# ---------------------------------------------------------------------------
# _resolve_venues tests
# ---------------------------------------------------------------------------


class TestResolveVenues:
    """Tests for the _resolve_venues helper."""

    def test_returns_cli_venues_as_clubspark_configs(self) -> None:
        """Wrap CLI venue slugs as ClubSpark VenueConfig objects."""
        result = _resolve_venues(["VenueA", "VenueB"])
        assert len(result) == TWO_VENUES
        assert all(isinstance(v, VenueConfig) for v in result)
        assert all(v.client == "clubspark" for v in result)
        assert [v.venue for v in result] == ["VenueA", "VenueB"]

    def test_falls_back_to_config_when_none(self) -> None:
        """Load venues from the YAML config when cli_venues is None."""
        with patch(
            "personal_project.apps.tennis_court_booker.run.get_default_venue_configs",
            return_value=[VENUE_CONFIG_CS],
        ):
            result = _resolve_venues(None)
        assert result == [VENUE_CONFIG_CS]

    def test_exits_when_config_raises(self) -> None:
        """Call sys.exit when the config file cannot be loaded."""
        with (
            patch(
                "personal_project.apps.tennis_court_booker.run.get_default_venue_configs",
                side_effect=FileNotFoundError("not found"),
            ),
            pytest.raises(SystemExit),
        ):
            _resolve_venues(None)


# ---------------------------------------------------------------------------
# _resolve_hours tests
# ---------------------------------------------------------------------------


class TestResolveHours:
    """Tests for the _resolve_hours helper."""

    def test_returns_cli_hours_when_provided(self) -> None:
        """Return the list passed on the CLI without touching the config file."""
        hours = [17, 18, 19]
        assert _resolve_hours(hours) == hours

    def test_falls_back_to_config_when_none(self) -> None:
        """Load hours from the YAML config when cli_hours is None."""
        with patch(
            "personal_project.apps.tennis_court_booker.run.get_valid_court_start_times",
            return_value=DEFAULT_HOURS,
        ):
            result = _resolve_hours(None)
        assert result == DEFAULT_HOURS

    def test_exits_when_config_raises(self) -> None:
        """Call sys.exit when the config file cannot be loaded."""
        with (
            patch(
                "personal_project.apps.tennis_court_booker.run.get_valid_court_start_times",
                side_effect=FileNotFoundError("not found"),
            ),
            pytest.raises(SystemExit),
        ):
            _resolve_hours(None)


# ---------------------------------------------------------------------------
# Output formatting tests
# ---------------------------------------------------------------------------


class TestBuildAvailabilityFrame:
    """Tests for the _build_availability_frame helper."""

    def test_returns_multiindex_columns(self) -> None:
        """Return a DataFrame whose columns are a (venue, court) MultiIndex."""
        frame = _build_availability_frame([(VENUE, _make_availability())])
        assert isinstance(frame.columns, pd.MultiIndex)
        assert frame.columns.names == ["venue", "court"]

    def test_index_is_time_strings(self) -> None:
        """Return a DataFrame whose index contains HH:MM time strings."""
        frame = _build_availability_frame([(VENUE, _make_availability())])
        assert frame.index.name == "time"
        assert "09:00" in frame.index

    def test_available_slot_has_available_value(self) -> None:
        """Store 'available' for a slot with is_available=True."""
        frame = _build_availability_frame([(VENUE, _make_availability(available=True))])
        assert frame.loc["09:00", (VENUE, "Court 1")] == "available"

    def test_booked_slot_has_booked_value(self) -> None:
        """Store 'booked' for a slot with is_available=False."""
        # drop_fully_booked=False so the booked column is retained
        frame = _build_availability_frame(
            [(VENUE, _make_availability(available=False))],
            drop_fully_booked=False,
        )
        assert frame.loc["09:00", (VENUE, "Court 1")] == "booked"

    def test_drop_fully_booked_removes_all_booked_columns(self) -> None:
        """Drop a court column when every slot for that court is booked."""
        frame = _build_availability_frame(
            [(VENUE, _make_availability(available=False))],
            drop_fully_booked=True,
        )
        assert frame.empty

    def test_drop_fully_booked_false_retains_booked_columns(self) -> None:
        """Retain all court columns when drop_fully_booked is False."""
        frame = _build_availability_frame(
            [(VENUE, _make_availability(available=False))],
            drop_fully_booked=False,
        )
        assert not frame.empty
        assert (VENUE, "Court 1") in frame.columns

    def test_mixed_court_not_dropped(self) -> None:
        """Keep a court column that has at least one available slot."""
        frame = _build_availability_frame([(VENUE, _make_mixed_availability())])
        assert (VENUE, "Court 1") in frame.columns

    def test_multiple_venues_produce_multiindex_columns(self) -> None:
        """Produce one column per (venue, court) pair across multiple venues."""
        two_cols = 2
        avail_a = _make_availability()
        avail_b = VenueAvailability(
            venue=VENUE_B,
            date=DATE,
            slots=[CourtSlot("Court 1", DATE, TIME_09, TIME_10, is_available=True)],
        )
        frame = _build_availability_frame([(VENUE, avail_a), (VENUE_B, avail_b)])
        assert len(frame.columns) == two_cols
        assert (VENUE, "Court 1") in frame.columns
        assert (VENUE_B, "Court 1") in frame.columns

    def test_empty_results_returns_empty_frame(self) -> None:
        """Return an empty DataFrame when no results are provided."""
        frame = _build_availability_frame([])
        assert frame.empty

    def test_empty_slots_returns_empty_frame(self) -> None:
        """Return an empty DataFrame when all venues have no slots."""
        va = VenueAvailability(venue=VENUE, date=DATE, slots=[])
        frame = _build_availability_frame([(VENUE, va)])
        assert frame.empty

    def test_index_is_sorted(self) -> None:
        """Return a DataFrame with the time index in ascending order."""
        frame = _build_availability_frame([(VENUE, _make_mixed_availability())])
        times = list(frame.index)
        assert times == sorted(times)


class TestPrintAvailability:
    """Tests for the _print_availability output helper."""

    def test_prints_venue_name(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Include the venue slug in the printed output."""
        _print_availability([(VENUE, _make_availability())])
        assert VENUE in capsys.readouterr().out

    def test_prints_date(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Include the date in the printed output."""
        _print_availability([(VENUE, _make_availability())])
        assert "2026-03-10" in capsys.readouterr().out

    def test_prints_available_status(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Show 'available' for an available slot."""
        _print_availability([(VENUE, _make_availability(available=True))])
        assert "available" in capsys.readouterr().out

    def test_prints_booked_status(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Show 'booked' for an unavailable slot when the court also has available slots."""
        _print_availability([(VENUE, _make_mixed_availability())])
        assert "booked" in capsys.readouterr().out

    def test_empty_slots_prints_no_slots(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Print a 'No slots found' message when there are no slots."""
        va = VenueAvailability(venue=VENUE, date=DATE, slots=[])
        _print_availability([(VENUE, va)])
        assert "No slots found" in capsys.readouterr().out


class TestPrintCheckResult:
    """Tests for the _print_check_result output helper."""

    def test_prints_available(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Print AVAILABLE when the slot is free."""
        _print_check_result(VENUE, "Court 1", TIME_09, available=True)
        assert "AVAILABLE" in capsys.readouterr().out

    def test_prints_not_available(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Print 'not available' when the slot is taken."""
        _print_check_result(VENUE, "Court 1", TIME_09, available=False)
        assert "not available" in capsys.readouterr().out

    def test_prints_venue(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Include the venue name in the output line."""
        _print_check_result(VENUE, "Court 1", TIME_09, available=True)
        assert VENUE in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Argument parser tests
# ---------------------------------------------------------------------------


class TestBuildParser:
    """Tests for the _build_parser argument parser."""

    def test_availability_command_parses_date(self) -> None:
        """Parse --date into a datetime.date for the availability sub-command."""
        parser = _build_parser()
        args = parser.parse_args(["availability", "--date", "2026-03-10"])
        assert args.date == DATE

    def test_availability_command_parses_venues(self) -> None:
        """Parse --venues into a list of strings."""
        parser = _build_parser()
        args = parser.parse_args(["availability", "--date", "2026-03-10", "--venues", "VenueA"])
        assert args.venues == ["VenueA"]

    def test_availability_venues_defaults_to_none(self) -> None:
        """Leave venues as None when --venues is omitted."""
        parser = _build_parser()
        args = parser.parse_args(["availability", "--date", "2026-03-10"])
        assert args.venues is None

    def test_availability_parses_hours(self) -> None:
        """Parse --hours into a list of integers for the availability sub-command."""
        parser = _build_parser()
        args = parser.parse_args(
            ["availability", "--date", "2026-03-10", "--hours", "17,18,19"]
        )
        assert args.hours == [17, 18, 19]

    def test_availability_hours_defaults_to_none(self) -> None:
        """Leave hours as None when --hours is omitted."""
        parser = _build_parser()
        args = parser.parse_args(["availability", "--date", "2026-03-10"])
        assert args.hours is None

    def test_check_command_parses_all_args(self) -> None:
        """Parse date, venue, court, and time for the check sub-command."""
        parser = _build_parser()
        args = parser.parse_args(
            [
                "check",
                "--date", "2026-03-10",
                "--venue", VENUE,
                "--court", "Court 1",
                "--time", "09:00",
            ]
        )
        assert args.date == DATE
        assert args.venue == VENUE
        assert args.court == "Court 1"
        assert args.time == TIME_09

    def test_check_command_requires_venue(self) -> None:
        """Exit with an error when --venue is omitted from the check sub-command."""
        parser = _build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(
                ["check", "--date", "2026-03-10", "--court", "Court 1", "--time", "09:00"]
            )


# ---------------------------------------------------------------------------
# Async runner tests
# ---------------------------------------------------------------------------


class TestRunAvailability:
    """Tests for the _run_availability async runner."""

    @pytest.mark.asyncio
    async def test_calls_get_venue_availability_for_each_clubspark_venue(self) -> None:
        """Call get_venue_availability once per ClubSpark venue."""
        mock_result = _make_availability()
        vc_a = VenueConfig(client="clubspark", venue="VenueA")
        vc_b = VenueConfig(client="clubspark", venue="VenueB")
        with patch(
            "personal_project.apps.tennis_court_booker.run.get_venue_availability",
            new=AsyncMock(return_value=mock_result),
        ) as mock_fn:
            await _run_availability([vc_a, vc_b], DATE, DEFAULT_HOURS)
        assert mock_fn.call_count == TWO_VENUES

    @pytest.mark.asyncio
    async def test_dispatches_better_com_to_better_service(self) -> None:
        """Call get_venue_availability_better for Better.com venues."""
        mock_result = _make_availability()
        with (
            patch(
                "personal_project.apps.tennis_court_booker.run.get_venue_availability",
                new=AsyncMock(return_value=mock_result),
            ) as cs_mock,
            patch(
                "personal_project.apps.tennis_court_booker.run.get_venue_availability_better",
                new=AsyncMock(return_value=mock_result),
            ) as bc_mock,
        ):
            await _run_availability([VENUE_CONFIG_BETTER], DATE, DEFAULT_HOURS)
        cs_mock.assert_not_called()
        bc_mock.assert_called_once()

    @pytest.mark.asyncio
    async def test_passes_hours_to_clubspark_service(self) -> None:
        """Forward the hours list to get_venue_availability as a keyword argument."""
        mock_result = _make_availability()
        hours = [17, 18]
        with patch(
            "personal_project.apps.tennis_court_booker.run.get_venue_availability",
            new=AsyncMock(return_value=mock_result),
        ) as mock_fn:
            await _run_availability([VENUE_CONFIG_CS], DATE, hours)
        mock_fn.assert_called_once_with(VENUE, DATE, hours=hours)

    @pytest.mark.asyncio
    async def test_prints_error_on_venue_exception(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Print an error line rather than crashing when a venue raises."""
        with patch(
            "personal_project.apps.tennis_court_booker.run.get_venue_availability",
            new=AsyncMock(side_effect=TimeoutError("timed out")),
        ):
            await _run_availability([VENUE_CONFIG_CS], DATE, DEFAULT_HOURS)
        assert "ERROR" in capsys.readouterr().out

    @pytest.mark.asyncio
    async def test_prints_hours_in_header(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Include the queried hours in the output header."""
        mock_result = _make_availability()
        with patch(
            "personal_project.apps.tennis_court_booker.run.get_venue_availability",
            new=AsyncMock(return_value=mock_result),
        ):
            await _run_availability([VENUE_CONFIG_CS], DATE, [17, 18])
        assert "17" in capsys.readouterr().out


class TestRunCheck:
    """Tests for the _run_check async runner."""

    @pytest.mark.asyncio
    async def test_calls_check_slot_availability(self) -> None:
        """Call check_slot_availability exactly once for the given venue."""
        with patch(
            "personal_project.apps.tennis_court_booker.run.check_slot_availability",
            new=AsyncMock(return_value=True),
        ) as mock_fn:
            await _run_check(VENUE, DATE, "Court 1", TIME_09)
        mock_fn.assert_called_once_with(VENUE, DATE, "Court 1", TIME_09)

    @pytest.mark.asyncio
    async def test_prints_available_result(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Print AVAILABLE when the check returns True."""
        with patch(
            "personal_project.apps.tennis_court_booker.run.check_slot_availability",
            new=AsyncMock(return_value=True),
        ):
            await _run_check(VENUE, DATE, "Court 1", TIME_09)
        assert "AVAILABLE" in capsys.readouterr().out

    @pytest.mark.asyncio
    async def test_prints_error_on_exception(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Print an error line rather than crashing when the service raises."""
        with patch(
            "personal_project.apps.tennis_court_booker.run.check_slot_availability",
            new=AsyncMock(side_effect=TimeoutError("timed out")),
        ):
            await _run_check(VENUE, DATE, "Court 1", TIME_09)
        assert "ERROR" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# TestRunBook
# ---------------------------------------------------------------------------


_BOOK_CARD = CardDetails(
    card_number="4929000000006",
    expiry_mmyy="1225",
    security_code="123",
    cardholder_name="Oliver Warwick",
    billing_first_name="Oliver",
    billing_last_name="Warwick",
    billing_address_line_one="1 Test Street",
    billing_city="London",
    billing_postcode="EC1A 1BB",
)
_BOOK_VENUE = "islington-tennis-centre"
_BOOK_ACTIVITY = "tennis-court-indoor"
_BOOK_KEY = "abc123"


class TestRunBook:
    """Tests for the _run_book async runner."""

    @pytest.mark.asyncio
    async def test_prints_booked_on_success(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Print 'Booked' when book_court_better returns a successful result."""
        with patch(
            "personal_project.apps.tennis_court_booker.run.book_court_better",
            new=AsyncMock(return_value=BookingResult(success=True, reference="ref123")),
        ):
            await _run_book(_BOOK_VENUE, _BOOK_ACTIVITY, DATE, TIME_09, _BOOK_KEY, _BOOK_CARD)
        assert "Booked" in capsys.readouterr().out

    @pytest.mark.asyncio
    async def test_prints_failed_on_failure(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Print 'Failed' when book_court_better returns an unsuccessful result."""
        with patch(
            "personal_project.apps.tennis_court_booker.run.book_court_better",
            new=AsyncMock(return_value=BookingResult(success=False, error="Declined")),
        ):
            await _run_book(_BOOK_VENUE, _BOOK_ACTIVITY, DATE, TIME_09, _BOOK_KEY, _BOOK_CARD)
        assert "Failed" in capsys.readouterr().out

    @pytest.mark.asyncio
    async def test_prints_error_on_exception(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Print ERROR when book_court_better raises an exception."""
        with patch(
            "personal_project.apps.tennis_court_booker.run.book_court_better",
            new=AsyncMock(side_effect=RuntimeError("auth failed")),
        ):
            await _run_book(_BOOK_VENUE, _BOOK_ACTIVITY, DATE, TIME_09, _BOOK_KEY, _BOOK_CARD)
        assert "ERROR" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# _resolve_card tests
# ---------------------------------------------------------------------------


class TestResolveCard:
    """Tests for the _resolve_card helper."""

    def test_returns_card_when_found(self) -> None:
        """Return the CardDetails from the helper when credentials are present."""
        with patch(
            "personal_project.apps.tennis_court_booker.run.CardCredentialHelper.get_card",
            return_value=_BOOK_CARD,
        ):
            result = _resolve_card()
        assert result == _BOOK_CARD

    def test_exits_when_no_card_found(self) -> None:
        """Call sys.exit when get_card returns None."""
        with (
            patch(
                "personal_project.apps.tennis_court_booker.run.CardCredentialHelper.get_card",
                return_value=None,
            ),
            pytest.raises(SystemExit),
        ):
            _resolve_card()


# ---------------------------------------------------------------------------
# _run_card_credentials_set / _run_card_credentials_delete tests
# ---------------------------------------------------------------------------


class TestRunCardCredentialsSet:
    """Tests for the _run_card_credentials_set function."""

    def test_calls_set_card_with_provided_values(self) -> None:
        """Call CardCredentialHelper.set_card with the values entered by the user."""
        inputs = [
            "4929000000006",  # card number (getpass)
            "1225",           # expiry (getpass)
            "123",            # cvv (getpass)
            "Oliver Warwick", # cardholder name
            "Oliver",         # billing first name
            "Warwick",        # billing last name
            "1 Test Street",  # billing address 1
            "",               # billing address 2 (optional)
            "London",         # billing city
            "EC1A 1BB",       # billing postcode
        ]
        with (
            patch("getpass.getpass", side_effect=inputs[:3]),
            patch("builtins.input", side_effect=inputs[3:]),
            patch(
                "personal_project.apps.tennis_court_booker.run.CardCredentialHelper.set_card"
            ) as mock_set,
        ):
            _run_card_credentials_set()
        mock_set.assert_called_once()
        saved: CardDetails = mock_set.call_args[0][0]
        assert saved.card_number == "4929000000006"
        assert saved.billing_city == "London"

    def test_prints_confirmation(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Print a confirmation message after saving credentials."""
        inputs = [
            "4929000000006", "1225", "123",
            "Oliver Warwick", "Oliver", "Warwick", "1 Test Street", "", "London", "EC1A 1BB",
        ]
        with (
            patch("getpass.getpass", side_effect=inputs[:3]),
            patch("builtins.input", side_effect=inputs[3:]),
            patch("personal_project.apps.tennis_court_booker.run.CardCredentialHelper.set_card"),
        ):
            _run_card_credentials_set()
        assert "saved" in capsys.readouterr().out.lower()


class TestRunCardCredentialsDelete:
    """Tests for the _run_card_credentials_delete function."""

    def test_deletes_when_confirmed(self) -> None:
        """Call CardCredentialHelper.delete_card when the user confirms with 'y'."""
        with (
            patch("builtins.input", return_value="y"),
            patch(
                "personal_project.apps.tennis_court_booker.run.CardCredentialHelper.delete_card"
            ) as mock_del,
        ):
            _run_card_credentials_delete()
        mock_del.assert_called_once()

    def test_does_not_delete_when_not_confirmed(self) -> None:
        """Not call delete_card when the user enters anything other than 'y'."""
        with (
            patch("builtins.input", return_value="n"),
            patch(
                "personal_project.apps.tennis_court_booker.run.CardCredentialHelper.delete_card"
            ) as mock_del,
        ):
            _run_card_credentials_delete()
        mock_del.assert_not_called()

    def test_prints_aborted_when_not_confirmed(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Print 'Aborted' when the user does not confirm."""
        with (
            patch("builtins.input", return_value=""),
            patch("personal_project.apps.tennis_court_booker.run.CardCredentialHelper.delete_card"),
        ):
            _run_card_credentials_delete()
        assert "Aborted" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# _build_parser — card-credentials subcommand tests
# ---------------------------------------------------------------------------


class TestBuildParserCardCredentials:
    """Tests for the card-credentials sub-command in _build_parser."""

    def test_card_credentials_set_parses(self) -> None:
        """Parse 'card-credentials set' without error."""
        parser = _build_parser()
        args = parser.parse_args(["card-credentials", "set"])
        assert args.command == "card-credentials"
        assert args.creds_action == "set"

    def test_card_credentials_delete_parses(self) -> None:
        """Parse 'card-credentials delete' without error."""
        parser = _build_parser()
        args = parser.parse_args(["card-credentials", "delete"])
        assert args.command == "card-credentials"
        assert args.creds_action == "delete"

    def test_book_does_not_require_card_args(self) -> None:
        """Parse 'book' without any card flags — card is loaded from keyring."""
        parser = _build_parser()
        args = parser.parse_args([
            "book",
            "--date", "2026-03-10",
            "--venue", "islington-tennis-centre",
            "--activity", "tennis-court-indoor",
            "--time", "18:00",
            "--key", "abc123",
        ])
        assert args.command == "book"
        assert not hasattr(args, "card_number")


# ---------------------------------------------------------------------------
# _resolve_clubspark_credentials tests
# ---------------------------------------------------------------------------


class TestResolveClubSparkCredentials:
    """Tests for the _resolve_clubspark_credentials helper."""

    def test_returns_credentials_when_found(self) -> None:
        """Return the (email, password) tuple from the helper when credentials are present."""
        with patch(
            "personal_project.apps.tennis_court_booker.run.ClubSparkCredentialHelper.get_credentials",
            return_value=("a@b.com", "pass"),
        ):
            result = _resolve_clubspark_credentials()
        assert result == ("a@b.com", "pass")

    def test_exits_when_no_credentials_found(self) -> None:
        """Call sys.exit when get_credentials returns None."""
        with (
            patch(
                "personal_project.apps.tennis_court_booker.run.ClubSparkCredentialHelper.get_credentials",
                return_value=None,
            ),
            pytest.raises(SystemExit),
        ):
            _resolve_clubspark_credentials()


# ---------------------------------------------------------------------------
# _run_book_clubspark tests
# ---------------------------------------------------------------------------

_CS_EMAIL = "oliver@example.com"
_CS_PASSWORD = "secret"


class TestRunBookClubSpark:
    """Tests for the _run_book_clubspark async runner."""

    @pytest.mark.asyncio
    async def test_prints_booked_on_success(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Print 'Booked' when book_court_clubspark returns a successful result."""
        with patch(
            "personal_project.apps.tennis_court_booker.run.book_court_clubspark",
            new=AsyncMock(return_value=BookingResult(success=True, reference="ref-cs")),
        ):
            await _run_book_clubspark(
                _BOOK_VENUE, DATE, "Crt 1", TIME_09, _CS_EMAIL, _CS_PASSWORD
            )
        assert "Booked" in capsys.readouterr().out

    @pytest.mark.asyncio
    async def test_prints_failed_on_failure(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Print 'Failed' when book_court_clubspark returns an unsuccessful result."""
        with patch(
            "personal_project.apps.tennis_court_booker.run.book_court_clubspark",
            new=AsyncMock(return_value=BookingResult(success=False, error="Slot taken")),
        ):
            await _run_book_clubspark(
                _BOOK_VENUE, DATE, "Crt 1", TIME_09, _CS_EMAIL, _CS_PASSWORD
            )
        assert "Failed" in capsys.readouterr().out

    @pytest.mark.asyncio
    async def test_prints_error_on_exception(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Print ERROR when book_court_clubspark raises an exception."""
        with patch(
            "personal_project.apps.tennis_court_booker.run.book_court_clubspark",
            new=AsyncMock(side_effect=RuntimeError("auth failed")),
        ):
            await _run_book_clubspark(
                _BOOK_VENUE, DATE, "Crt 1", TIME_09, _CS_EMAIL, _CS_PASSWORD
            )
        assert "ERROR" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# _run_clubspark_credentials_set / delete tests
# ---------------------------------------------------------------------------


class TestRunClubSparkCredentialsSet:
    """Tests for the _run_clubspark_credentials_set function."""

    def test_calls_set_credentials_with_provided_values(self) -> None:
        """Call ClubSparkCredentialHelper.set_credentials with the entered values."""
        with (
            patch("builtins.input", return_value="oliver@example.com"),
            patch("getpass.getpass", return_value="secret"),
            patch(
                "personal_project.apps.tennis_court_booker.run.ClubSparkCredentialHelper.set_credentials"
            ) as mock_set,
        ):
            _run_clubspark_credentials_set()
        mock_set.assert_called_once_with("oliver@example.com", "secret")

    def test_prints_confirmation(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Print a confirmation message after saving credentials."""
        with (
            patch("builtins.input", return_value="oliver@example.com"),
            patch("getpass.getpass", return_value="secret"),
            patch("personal_project.apps.tennis_court_booker.run.ClubSparkCredentialHelper.set_credentials"),
        ):
            _run_clubspark_credentials_set()
        assert "saved" in capsys.readouterr().out.lower()


class TestRunClubSparkCredentialsDelete:
    """Tests for the _run_clubspark_credentials_delete function."""

    def test_deletes_when_confirmed(self) -> None:
        """Call ClubSparkCredentialHelper.delete_credentials when the user confirms."""
        with (
            patch("builtins.input", side_effect=["oliver@example.com", "y"]),
            patch(
                "personal_project.apps.tennis_court_booker.run.ClubSparkCredentialHelper.delete_credentials"
            ) as mock_del,
        ):
            _run_clubspark_credentials_delete()
        mock_del.assert_called_once_with("oliver@example.com")

    def test_does_not_delete_when_not_confirmed(self) -> None:
        """Not call delete_credentials when the user does not confirm."""
        with (
            patch("builtins.input", side_effect=["oliver@example.com", "n"]),
            patch(
                "personal_project.apps.tennis_court_booker.run.ClubSparkCredentialHelper.delete_credentials"
            ) as mock_del,
        ):
            _run_clubspark_credentials_delete()
        mock_del.assert_not_called()


# ---------------------------------------------------------------------------
# _build_parser — clubspark-credentials and updated book subcommand tests
# ---------------------------------------------------------------------------


class TestBuildParserClubSparkExtensions:
    """Tests for ClubSpark-related additions to _build_parser."""

    def test_clubspark_credentials_set_parses(self) -> None:
        """Parse 'clubspark-credentials set' without error."""
        parser = _build_parser()
        args = parser.parse_args(["clubspark-credentials", "set"])
        assert args.command == "clubspark-credentials"
        assert args.cs_creds_action == "set"

    def test_clubspark_credentials_delete_parses(self) -> None:
        """Parse 'clubspark-credentials delete' without error."""
        parser = _build_parser()
        args = parser.parse_args(["clubspark-credentials", "delete"])
        assert args.command == "clubspark-credentials"
        assert args.cs_creds_action == "delete"

    def test_book_with_court_parses_for_clubspark(self) -> None:
        """Parse 'book --court' as a ClubSpark booking."""
        parser = _build_parser()
        args = parser.parse_args([
            "book",
            "--date", "2026-03-14",
            "--venue", "BurgessParkSouthwark",
            "--time", "19:00",
            "--court", "Crt 1",
        ])
        assert args.court == "Crt 1"
        assert args.activity is None
        assert args.key is None

    def test_book_with_activity_and_key_parses_for_better(self) -> None:
        """Parse 'book --activity --key' as a Better.com booking."""
        parser = _build_parser()
        args = parser.parse_args([
            "book",
            "--date", "2026-03-14",
            "--venue", "islington-tennis-centre",
            "--time", "19:00",
            "--activity", "tennis-court-indoor",
            "--key", "abc123",
        ])
        assert args.activity == "tennis-court-indoor"
        assert args.key == "abc123"
        assert args.court is None
