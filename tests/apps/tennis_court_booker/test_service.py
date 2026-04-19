"""Tests for the tennis court booker service layer.

.. note::
    The ``book_court_clubspark`` tests are grouped in
    :class:`TestBookCourtClubSpark` at the bottom of this module.

Verifies that :func:`get_venue_availability`, :func:`get_venue_availability_better`,
:func:`check_slot_availability`, and :func:`book_court_better` correctly orchestrate
their respective clients and translate raw slot data into domain objects.  All
network clients are replaced with mocks so these tests run without a browser or
network connection.
"""

from __future__ import annotations

import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from personal_project.apps.tennis_court_booker.models import (
    BookingResult,
    CardDetails,
    CourtSlot,
    VenueAvailability,
)
from personal_project.apps.tennis_court_booker.service import (
    book_court_better,
    book_court_clubspark,
    check_slot_availability,
    get_venue_availability,
    get_venue_availability_better,
)
from personal_project.clients.clubspark.client import RawSlot

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

VENUE = "BurgessParkSouthwark"
DATE = datetime.date(2026, 3, 10)
TIME_09 = datetime.time(9, 0)
TIME_10 = datetime.time(10, 0)
TIME_11 = datetime.time(11, 0)
EXPECTED_SLOT_COUNT = 2


def _raw(
    court: str,
    start: datetime.time,
    end: datetime.time,
    *,
    available: bool,
) -> RawSlot:
    """Build a RawSlot for use in test doubles.

    Args:
        court: Court name string.
        start: Slot start time.
        end: Slot end time.
        available: Whether the slot is available.

    Returns:
        A :class:`RawSlot` instance.

    """
    return RawSlot(court_name=court, start_time=start, end_time=end, is_available=available)


@pytest.fixture
def mock_client() -> MagicMock:
    """Return a MagicMock standing in for ClubSparkClient.

    The ``get_available_slots`` and ``is_slot_available`` methods are
    configured as :class:`~unittest.mock.AsyncMock` so they can be awaited.

    Returns:
        A :class:`~unittest.mock.MagicMock` with async method doubles.

    """
    client = MagicMock()
    client.get_available_slots = AsyncMock()
    client.is_slot_available = AsyncMock()
    return client


# ---------------------------------------------------------------------------
# get_venue_availability tests
# ---------------------------------------------------------------------------


class TestGetVenueAvailability:
    """Tests for the get_venue_availability service function."""

    @pytest.mark.asyncio
    async def test_returns_venue_availability_instance(
        self, mock_client: MagicMock
    ) -> None:
        """Return a VenueAvailability object."""
        mock_client.get_available_slots.return_value = []
        result = await get_venue_availability(VENUE, DATE, client=mock_client)
        assert isinstance(result, VenueAvailability)

    @pytest.mark.asyncio
    async def test_venue_and_date_propagated(self, mock_client: MagicMock) -> None:
        """Propagate venue and date to the returned VenueAvailability."""
        mock_client.get_available_slots.return_value = []
        result = await get_venue_availability(VENUE, DATE, client=mock_client)
        assert result.venue == VENUE
        assert result.date == DATE

    @pytest.mark.asyncio
    async def test_raw_slots_converted_to_court_slots(
        self, mock_client: MagicMock
    ) -> None:
        """Convert all raw slots from the client into CourtSlot domain objects."""
        raw_slots = [
            _raw("Court 1", TIME_09, TIME_10, available=True),
            _raw("Court 2", TIME_09, TIME_10, available=False),
        ]
        mock_client.get_available_slots.return_value = raw_slots
        result = await get_venue_availability(VENUE, DATE, client=mock_client)
        assert len(result.slots) == EXPECTED_SLOT_COUNT
        assert all(isinstance(s, CourtSlot) for s in result.slots)

    @pytest.mark.asyncio
    async def test_date_attached_to_slots(self, mock_client: MagicMock) -> None:
        """Set the date field on each converted CourtSlot."""
        mock_client.get_available_slots.return_value = [
            _raw("Court 1", TIME_09, TIME_10, available=True),
        ]
        result = await get_venue_availability(VENUE, DATE, client=mock_client)
        assert result.slots[0].date == DATE

    @pytest.mark.asyncio
    async def test_availability_flag_preserved(self, mock_client: MagicMock) -> None:
        """Preserve the is_available flag when converting raw to domain slots."""
        mock_client.get_available_slots.return_value = [
            _raw("Court 1", TIME_09, TIME_10, available=True),
            _raw("Court 1", TIME_10, TIME_11, available=False),
        ]
        result = await get_venue_availability(VENUE, DATE, client=mock_client)
        assert result.slots[0].is_available is True
        assert result.slots[1].is_available is False

    @pytest.mark.asyncio
    async def test_client_called_with_venue_and_date(
        self, mock_client: MagicMock
    ) -> None:
        """Pass the correct venue and date arguments to the client."""
        mock_client.get_available_slots.return_value = []
        await get_venue_availability(VENUE, DATE, client=mock_client)
        mock_client.get_available_slots.assert_called_once_with(VENUE, DATE)

    @pytest.mark.asyncio
    async def test_empty_grid_returns_empty_slots(
        self, mock_client: MagicMock
    ) -> None:
        """Return a VenueAvailability with an empty slots list when the grid is empty."""
        mock_client.get_available_slots.return_value = []
        result = await get_venue_availability(VENUE, DATE, client=mock_client)
        assert result.slots == []


# ---------------------------------------------------------------------------
# check_slot_availability tests
# ---------------------------------------------------------------------------


class TestCheckSlotAvailability:
    """Tests for the check_slot_availability service function."""

    @pytest.mark.asyncio
    async def test_returns_true_when_available(self, mock_client: MagicMock) -> None:
        """Return True when the client reports the slot is available."""
        mock_client.is_slot_available.return_value = True
        result = await check_slot_availability(
            VENUE, DATE, "Court 1", TIME_09, client=mock_client
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_unavailable(
        self, mock_client: MagicMock
    ) -> None:
        """Return False when the client reports the slot is not available."""
        mock_client.is_slot_available.return_value = False
        result = await check_slot_availability(
            VENUE, DATE, "Court 1", TIME_09, client=mock_client
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_client_called_with_correct_args(
        self, mock_client: MagicMock
    ) -> None:
        """Pass venue, date, court name, and start time to the client."""
        mock_client.is_slot_available.return_value = True
        await check_slot_availability(
            VENUE, DATE, "Court 1", TIME_09, client=mock_client
        )
        mock_client.is_slot_available.assert_called_once_with(
            VENUE, DATE, "Court 1", TIME_09
        )


# ---------------------------------------------------------------------------
# get_venue_availability_better tests
# ---------------------------------------------------------------------------

BETTER_VENUE = "islington-tennis-centre"
BETTER_ACTIVITY = "tennis-court-indoor"

_SAMPLE_RAW_SLOT: dict[str, object] = {
    "date": "2026-03-10",
    "start_iso": "2026-03-10T09:00:00",
    "end_iso": "2026-03-10T10:00:00",
    "court_name": "Tennis Court - Indoor",
    "court_id": "ck1",
    "is_available": True,
    "price": "£40.00",
    "source_endpoint": "https://better-admin.org.uk/...",
}


@pytest.fixture
def mock_better_client() -> MagicMock:
    """Return a MagicMock standing in for BetterClient.

    ``ensure_logged_in`` returns ``True`` and ``get_availability`` returns a
    single sample slot.  Both are synchronous methods (the service wraps them
    in :func:`asyncio.to_thread`).

    Returns:
        A :class:`~unittest.mock.MagicMock` configured for happy-path testing.

    """
    client = MagicMock()
    client.ensure_logged_in.return_value = True
    client.get_availability.return_value = [dict(_SAMPLE_RAW_SLOT)]
    return client


class TestGetVenueAvailabilityBetter:
    """Tests for the get_venue_availability_better service function."""

    @pytest.mark.asyncio
    async def test_returns_venue_availability_instance(
        self, mock_better_client: MagicMock
    ) -> None:
        """Return a VenueAvailability object."""
        result = await get_venue_availability_better(
            BETTER_VENUE, BETTER_ACTIVITY, DATE, client=mock_better_client
        )
        assert isinstance(result, VenueAvailability)

    @pytest.mark.asyncio
    async def test_venue_and_date_propagated(self, mock_better_client: MagicMock) -> None:
        """Propagate venue and date to the returned VenueAvailability."""
        result = await get_venue_availability_better(
            BETTER_VENUE, BETTER_ACTIVITY, DATE, client=mock_better_client
        )
        assert result.venue == BETTER_VENUE
        assert result.date == DATE

    @pytest.mark.asyncio
    async def test_raw_slots_converted_to_court_slots(
        self, mock_better_client: MagicMock
    ) -> None:
        """Convert Better.com raw dicts into CourtSlot domain objects."""
        result = await get_venue_availability_better(
            BETTER_VENUE, BETTER_ACTIVITY, DATE, client=mock_better_client
        )
        assert len(result.slots) == 1
        assert isinstance(result.slots[0], CourtSlot)
        assert result.slots[0].court_name == "Tennis Court - Indoor"
        assert result.slots[0].start_time == TIME_09
        assert result.slots[0].end_time == TIME_10

    @pytest.mark.asyncio
    async def test_slot_with_missing_start_iso_is_skipped(
        self, mock_better_client: MagicMock
    ) -> None:
        """Skip raw slots that are missing start_iso."""
        bad = dict(_SAMPLE_RAW_SLOT)
        bad["start_iso"] = None
        mock_better_client.get_availability.return_value = [bad]
        result = await get_venue_availability_better(
            BETTER_VENUE, BETTER_ACTIVITY, DATE, client=mock_better_client
        )
        assert result.slots == []

    @pytest.mark.asyncio
    async def test_raises_runtime_error_when_login_fails(
        self, mock_better_client: MagicMock
    ) -> None:
        """Raise RuntimeError when ensure_logged_in returns False."""
        mock_better_client.ensure_logged_in.return_value = False
        with pytest.raises(RuntimeError, match="authentication failed"):
            await get_venue_availability_better(
                BETTER_VENUE, BETTER_ACTIVITY, DATE, client=mock_better_client
            )

    @pytest.mark.asyncio
    async def test_hours_filter_applied(self, mock_better_client: MagicMock) -> None:
        """Exclude slots whose start hour is not in the hours filter."""
        slot_09 = dict(_SAMPLE_RAW_SLOT)  # starts at 09:00 -> hour 9
        slot_17 = dict(_SAMPLE_RAW_SLOT)
        slot_17["start_iso"] = "2026-03-10T17:00:00"
        slot_17["end_iso"] = "2026-03-10T18:00:00"
        mock_better_client.get_availability.return_value = [slot_09, slot_17]
        result = await get_venue_availability_better(
            BETTER_VENUE, BETTER_ACTIVITY, DATE, hours=[17], client=mock_better_client
        )
        assert len(result.slots) == 1
        assert result.slots[0].start_time.hour == 17  # noqa: PLR2004

    @pytest.mark.asyncio
    async def test_empty_availability_returns_empty_slots(
        self, mock_better_client: MagicMock
    ) -> None:
        """Return empty slots list when the API returns no data."""
        mock_better_client.get_availability.return_value = []
        result = await get_venue_availability_better(
            BETTER_VENUE, BETTER_ACTIVITY, DATE, client=mock_better_client
        )
        assert result.slots == []


# ---------------------------------------------------------------------------
# book_court_better tests
# ---------------------------------------------------------------------------

_CARD = CardDetails(
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
_START = datetime.time(18, 0)
_COMPOSITE_KEY = "abc123"
_SLOT_ID = 99999
_PRICING_OPTION_ID = 1161
_USER_ID = 1741614
_CART_ITEM_ID = 55555
_ITEM_HASH = "aGFzaA=="
_SESSION_KEY = "SESSION-KEY-UUID"
_OPAYO_URL = "https://live.opayo.eu.elavon.com"
_CARD_IDENTIFIER = "CARD-ID-UUID"
_TX_UUID = "tx-uuid-abc"


_MISSING = object()  # sentinel for "use default" vs explicit None


def _make_booking_client(
    *,
    logged_in: bool = True,
    user_id: int | None = _USER_ID,
    slot: object = _MISSING,
    cart: object = _MISSING,
    prep: object = _MISSING,
    card_identifier: str | None = _CARD_IDENTIFIER,
    auth_resp: object = _MISSING,
) -> MagicMock:
    """Return a configured MagicMock BetterClient for booking flow tests.

    Pass ``None`` explicitly to simulate a method that returns ``None`` (i.e.
    a failure path).  Omit a parameter to use the default happy-path value.

    Args:
        logged_in: Whether ``ensure_logged_in`` returns ``True``.
        user_id: Value returned by ``get_user_id``.
        slot: Value returned by ``get_slot_details``.  Defaults to a valid
            slot dict; pass ``None`` to simulate a missing slot.
        cart: Value returned by ``add_to_cart``.  Pass ``None`` to simulate
            a cart failure.
        prep: Value returned by ``checkout_prepare``.  Pass ``None`` to
            simulate a prepare failure.
        card_identifier: Value returned by ``tokenise_card_opayo``.
        auth_resp: Value returned by ``checkout_authorise``.  Defaults to a
            successful authorisation response.

    Returns:
        A :class:`~unittest.mock.MagicMock` configured for the requested
        scenario.

    """
    client = MagicMock()
    client.ensure_logged_in.return_value = logged_in
    client.get_user_id.return_value = user_id
    client.get_slot_details.return_value = (
        {"id": _SLOT_ID, "pricing_option_id": _PRICING_OPTION_ID}
        if slot is _MISSING else slot
    )
    client.add_to_cart.return_value = (
        {"data": {"itemHash": _ITEM_HASH, "items": [{"id": _CART_ITEM_ID}]}}
        if cart is _MISSING else cart
    )
    client.checkout_prepare.return_value = (
        {"session_key": _SESSION_KEY, "payment_provider_configuration": {"url": _OPAYO_URL}}
        if prep is _MISSING else prep
    )
    client.tokenise_card_opayo.return_value = card_identifier
    client.checkout_authorise.return_value = (
        {"transaction_uuid": _TX_UUID, "transaction_status": "ok", "sca_url": None, "error": None}
        if auth_resp is _MISSING else auth_resp
    )
    client.remove_from_cart.return_value = True
    return client


class TestBookCourtBetter:
    """Tests for the book_court_better service function."""

    @pytest.mark.asyncio
    async def test_returns_booking_result_on_success(self) -> None:
        """Return a successful BookingResult with a transaction reference."""
        with patch("personal_project.apps.tennis_court_booker.service.asyncio.to_thread",
                   side_effect=lambda f, *a, **kw: f(*a, **kw)):
            result = await book_court_better(
                BETTER_VENUE, BETTER_ACTIVITY, DATE, _START, _COMPOSITE_KEY, _CARD,
                client=_make_booking_client(),
            )
        assert isinstance(result, BookingResult)
        assert result.success is True
        assert result.reference == _TX_UUID

    @pytest.mark.asyncio
    async def test_raises_runtime_error_when_login_fails(self) -> None:
        """Raise RuntimeError when ensure_logged_in returns False."""
        with (
            patch("personal_project.apps.tennis_court_booker.service.asyncio.to_thread",
                  side_effect=lambda f, *a, **kw: f(*a, **kw)),
            pytest.raises(RuntimeError, match="authentication failed"),
        ):
            await book_court_better(
                BETTER_VENUE, BETTER_ACTIVITY, DATE, _START, _COMPOSITE_KEY, _CARD,
                client=_make_booking_client(logged_in=False),
            )

    @pytest.mark.asyncio
    async def test_returns_failure_when_user_id_missing(self) -> None:
        """Return failure BookingResult when user ID cannot be retrieved."""
        with patch("personal_project.apps.tennis_court_booker.service.asyncio.to_thread",
                   side_effect=lambda f, *a, **kw: f(*a, **kw)):
            result = await book_court_better(
                BETTER_VENUE, BETTER_ACTIVITY, DATE, _START, _COMPOSITE_KEY, _CARD,
                client=_make_booking_client(user_id=None),
            )
        assert result.success is False
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_returns_failure_when_slot_not_found(self) -> None:
        """Return failure BookingResult when get_slot_details returns None."""
        with patch("personal_project.apps.tennis_court_booker.service.asyncio.to_thread",
                   side_effect=lambda f, *a, **kw: f(*a, **kw)):
            result = await book_court_better(
                BETTER_VENUE, BETTER_ACTIVITY, DATE, _START, _COMPOSITE_KEY, _CARD,
                client=_make_booking_client(slot=None),
            )
        assert result.success is False

    @pytest.mark.asyncio
    async def test_returns_failure_when_cart_add_fails(self) -> None:
        """Return failure BookingResult when add_to_cart returns None."""
        with patch("personal_project.apps.tennis_court_booker.service.asyncio.to_thread",
                   side_effect=lambda f, *a, **kw: f(*a, **kw)):
            result = await book_court_better(
                BETTER_VENUE, BETTER_ACTIVITY, DATE, _START, _COMPOSITE_KEY, _CARD,
                client=_make_booking_client(cart=None),
            )
        assert result.success is False

    @pytest.mark.asyncio
    async def test_returns_failure_when_prepare_fails(self) -> None:
        """Return failure BookingResult when checkout_prepare returns None."""
        with patch("personal_project.apps.tennis_court_booker.service.asyncio.to_thread",
                   side_effect=lambda f, *a, **kw: f(*a, **kw)):
            result = await book_court_better(
                BETTER_VENUE, BETTER_ACTIVITY, DATE, _START, _COMPOSITE_KEY, _CARD,
                client=_make_booking_client(prep=None),
            )
        assert result.success is False

    @pytest.mark.asyncio
    async def test_returns_failure_when_tokenisation_fails(self) -> None:
        """Return failure BookingResult when Opayo card tokenisation returns None."""
        with patch("personal_project.apps.tennis_court_booker.service.asyncio.to_thread",
                   side_effect=lambda f, *a, **kw: f(*a, **kw)):
            result = await book_court_better(
                BETTER_VENUE, BETTER_ACTIVITY, DATE, _START, _COMPOSITE_KEY, _CARD,
                client=_make_booking_client(card_identifier=None),
            )
        assert result.success is False

    @pytest.mark.asyncio
    async def test_returns_failure_when_bank_declines(self) -> None:
        """Return failure BookingResult when authorise reports a bank decline."""
        declined = {
            "transaction_uuid": "tx-declined",
            "transaction_status": "failed",
            "sca_url": None,
            "error": {"message": "The Authorisation was Declined by the bank.", "code": "2000"},
        }
        with patch("personal_project.apps.tennis_court_booker.service.asyncio.to_thread",
                   side_effect=lambda f, *a, **kw: f(*a, **kw)):
            result = await book_court_better(
                BETTER_VENUE, BETTER_ACTIVITY, DATE, _START, _COMPOSITE_KEY, _CARD,
                client=_make_booking_client(auth_resp=declined),
            )
        assert result.success is False
        assert "Declined" in (result.error or "")

    @pytest.mark.asyncio
    async def test_returns_failure_when_sca_required(self) -> None:
        """Return failure BookingResult when a 3DS SCA challenge is required."""
        sca = {
            "transaction_uuid": "tx-sca",
            "transaction_status": "pending",
            "sca_url": "https://3ds.example.com/challenge",
            "error": None,
        }
        with patch("personal_project.apps.tennis_court_booker.service.asyncio.to_thread",
                   side_effect=lambda f, *a, **kw: f(*a, **kw)):
            result = await book_court_better(
                BETTER_VENUE, BETTER_ACTIVITY, DATE, _START, _COMPOSITE_KEY, _CARD,
                client=_make_booking_client(auth_resp=sca),
            )
        assert result.success is False
        assert "3D Secure" in (result.error or "")

# ---------------------------------------------------------------------------
# book_court_clubspark tests
# ---------------------------------------------------------------------------

_CS_EMAIL = "oliver@example.com"
_CS_PASSWORD = "secret"


def _make_cs_client(
    *,
    reference: str | None = "ref-cs-123",
    raises: Exception | None = None,
) -> MagicMock:
    """Return a configured AsyncMock ClubSparkClient for booking tests.

    Args:
        reference: Value returned by ``book_slot``.  Pass ``None`` to simulate
            a booking that succeeds but returns no reference.
        raises: If provided, ``book_slot`` will raise this exception instead.

    Returns:
        A :class:`~unittest.mock.MagicMock` with an async ``book_slot`` method.

    """
    client = MagicMock()
    if raises is not None:
        client.book_slot = AsyncMock(side_effect=raises)
    else:
        client.book_slot = AsyncMock(return_value=reference)
    return client


class TestBookCourtClubSpark:
    """Tests for the book_court_clubspark service function."""

    @pytest.mark.asyncio
    async def test_returns_success_with_reference(self) -> None:
        """Return a successful BookingResult containing the reference string."""
        result = await book_court_clubspark(
            VENUE, DATE, "Crt 1", TIME_09,
            email=_CS_EMAIL, password=_CS_PASSWORD,
            client=_make_cs_client(reference="ref-cs-123"),
        )
        assert isinstance(result, BookingResult)
        assert result.success is True
        assert result.reference == "ref-cs-123"

    @pytest.mark.asyncio
    async def test_returns_success_with_none_reference(self) -> None:
        """Return success even when book_slot returns None (no reference element found)."""
        result = await book_court_clubspark(
            VENUE, DATE, "Crt 1", TIME_09,
            email=_CS_EMAIL, password=_CS_PASSWORD,
            client=_make_cs_client(reference=None),
        )
        assert result.success is True
        assert result.reference is None

    @pytest.mark.asyncio
    async def test_returns_failure_on_runtime_error(self) -> None:
        """Return failure BookingResult when book_slot raises RuntimeError."""
        result = await book_court_clubspark(
            VENUE, DATE, "Crt 1", TIME_09,
            email=_CS_EMAIL, password=_CS_PASSWORD,
            client=_make_cs_client(raises=RuntimeError("Slot not found")),
        )
        assert result.success is False
        assert "Slot not found" in (result.error or "")

    @pytest.mark.asyncio
    async def test_returns_failure_on_timeout_error(self) -> None:
        """Return failure BookingResult when book_slot raises TimeoutError."""
        result = await book_court_clubspark(
            VENUE, DATE, "Crt 1", TIME_09,
            email=_CS_EMAIL, password=_CS_PASSWORD,
            client=_make_cs_client(raises=TimeoutError("Grid did not appear")),
        )
        assert result.success is False
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_book_slot_called_with_correct_args(self) -> None:
        """Call client.book_slot with the expected venue, date, court, time, and credentials."""
        cs_client = _make_cs_client()
        await book_court_clubspark(
            VENUE, DATE, "Crt 1", TIME_09,
            email=_CS_EMAIL, password=_CS_PASSWORD,
            client=cs_client,
        )
        cs_client.book_slot.assert_called_once_with(
            VENUE, DATE, "Crt 1", TIME_09,
            email=_CS_EMAIL, password=_CS_PASSWORD,
        )
