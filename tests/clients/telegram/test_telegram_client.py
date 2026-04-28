"""
Tests for the Telegram notification client.

All Telegram Bot API calls are mocked via respx — no real requests are made.
"""

from datetime import UTC, date, datetime
from unittest.mock import patch

import httpx
import pytest
import respx

from src.clients.telegram.client import TelegramClient, TelegramError
from src.store.rightmove import StoredProperty

FAKE_TOKEN = "123456:FAKE_TOKEN_FOR_TESTS"
SEND_MESSAGE_URL = f"https://api.telegram.org/bot{FAKE_TOKEN}/sendMessage"


def _make_stored_property(
    property_id: int = 111111111,
    address: str = "Wandsworth High Street, SW18",
    price: int = 2200,
    bedrooms: int = 2,
    listing_date: date | None = None,
) -> StoredProperty:
    return StoredProperty(
        url_hash="abc123",
        rightmove_id=property_id,
        url=f"https://www.rightmove.co.uk/properties/{property_id}",
        discovered_at=datetime(2026, 4, 8, 16, 54, tzinfo=UTC),
        listing_date=listing_date or date(2026, 4, 8),
        search_name="Wandsworth 2-bed rental",
        location_identifier="REGION^96855",
        display_address=address,
        price_amount=price,
        price_currency="GBP",
        price_frequency="monthly",
        bedrooms=bedrooms,
        bathrooms=1,
        property_type="Flat",
        summary="A nice flat",
        let_available_date="Now",
        added_or_reduced="Added today",
        thumbnail_url=None,
    )


def _ok_response(message_id: int = 1) -> httpx.Response:
    return httpx.Response(200, json={"ok": True, "result": {"message_id": message_id}})


# ---------------------------------------------------------------------------
# Unit tests — formatting
# ---------------------------------------------------------------------------


class TestFormatListing:
    def test_includes_address(self):
        prop = _make_stored_property(address="High Street, SW18")
        text = TelegramClient._format_listing(prop)
        assert "High Street, SW18" in text

    def test_includes_price(self):
        prop = _make_stored_property(price=2500)
        text = TelegramClient._format_listing(prop)
        assert "£2,500" in text
        assert "monthly" in text

    def test_includes_bedrooms(self):
        prop = _make_stored_property(bedrooms=2)
        text = TelegramClient._format_listing(prop)
        assert "2 bed" in text

    def test_includes_url(self):
        prop = _make_stored_property(property_id=111111111)
        text = TelegramClient._format_listing(prop)
        assert "https://www.rightmove.co.uk/properties/111111111" in text

    def test_includes_listing_date(self):
        prop = _make_stored_property(listing_date=date(2026, 4, 1))
        text = TelegramClient._format_listing(prop)
        assert "2026-04-01" in text

    def test_uses_html_bold_for_address(self):
        prop = _make_stored_property()
        text = TelegramClient._format_listing(prop)
        assert "<b>" in text and "</b>" in text

    def test_no_bedrooms_shows_studio(self):
        prop = _make_stored_property(bedrooms=0)
        prop.bedrooms = None
        text = TelegramClient._format_listing(prop)
        assert "Studio" in text or "unknown" in text


# ---------------------------------------------------------------------------
# Integration tests — API calls
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestSendMessage:
    @respx.mock
    async def test_send_message_calls_api(self):
        route = respx.post(SEND_MESSAGE_URL).mock(return_value=_ok_response())

        async with TelegramClient(token=FAKE_TOKEN) as tg:
            await tg.send_message(chat_id="@testchannel", text="Hello")

        assert route.called
        payload = route.calls[0].request
        import json
        body = json.loads(payload.content)
        assert body["chat_id"] == "@testchannel"
        assert body["text"] == "Hello"
        assert body["parse_mode"] == "HTML"

    @respx.mock
    async def test_send_message_raises_on_api_error(self):
        respx.post(SEND_MESSAGE_URL).mock(
            return_value=httpx.Response(200, json={"ok": False, "description": "Bad Request"})
        )

        async with TelegramClient(token=FAKE_TOKEN) as tg:
            with pytest.raises(TelegramError, match="Bad Request"):
                await tg.send_message(chat_id="@testchannel", text="Hello")

    @respx.mock
    async def test_send_listing_sends_formatted_message(self):
        route = respx.post(SEND_MESSAGE_URL).mock(return_value=_ok_response())
        prop = _make_stored_property(address="Ram Street, SW18", price=1750)

        async with TelegramClient(token=FAKE_TOKEN) as tg:
            with patch("asyncio.sleep"):
                await tg.send_listing(chat_id="@testchannel", prop=prop)

        assert route.called
        import json
        body = json.loads(route.calls[0].request.content)
        assert "Ram Street, SW18" in body["text"]
        assert "£1,750" in body["text"]


@pytest.mark.asyncio
class TestSendListingsBatch:
    @respx.mock
    async def test_batch_sends_header_plus_one_per_listing(self):
        route = respx.post(SEND_MESSAGE_URL).mock(return_value=_ok_response())
        props = [_make_stored_property(i) for i in [111, 222, 333]]

        async with TelegramClient(token=FAKE_TOKEN) as tg:
            with patch("asyncio.sleep"):
                sent = await tg.send_listings_batch(
                    chat_id="@testchannel",
                    properties=props,
                    search_name="Wandsworth",
                )

        # 1 header + 3 listings
        assert route.call_count == 4
        assert sent == 3

    @respx.mock
    async def test_batch_header_contains_count_and_search_name(self):
        route = respx.post(SEND_MESSAGE_URL).mock(return_value=_ok_response())
        props = [_make_stored_property(111), _make_stored_property(222)]

        async with TelegramClient(token=FAKE_TOKEN) as tg:
            with patch("asyncio.sleep"):
                await tg.send_listings_batch(
                    chat_id="@testchannel",
                    properties=props,
                    search_name="Wandsworth",
                )

        import json
        header_text = json.loads(route.calls[0].request.content)["text"]
        assert "2" in header_text
        assert "Wandsworth" in header_text

    @respx.mock
    async def test_batch_returns_zero_for_empty_list(self):
        respx.post(SEND_MESSAGE_URL).mock(return_value=_ok_response())

        async with TelegramClient(token=FAKE_TOKEN) as tg:
            sent = await tg.send_listings_batch(chat_id="@testchannel", properties=[])

        assert sent == 0

    @respx.mock
    async def test_batch_continues_after_single_failure(self):
        call_count = 0

        def _side_effect(request):
            nonlocal call_count
            call_count += 1
            if call_count == 2:  # fail on the first listing (after header)
                return httpx.Response(200, json={"ok": False, "description": "Rate limited"})
            return _ok_response(call_count)

        respx.post(SEND_MESSAGE_URL).mock(side_effect=_side_effect)
        props = [_make_stored_property(111), _make_stored_property(222)]

        async with TelegramClient(token=FAKE_TOKEN) as tg:
            with patch("asyncio.sleep"):
                sent = await tg.send_listings_batch(
                    chat_id="@testchannel",
                    properties=props,
                )

        # Should have sent the header + prop2 (prop1 failed)
        assert sent == 1

    @respx.mock
    async def test_batch_singular_header_for_one_listing(self):
        route = respx.post(SEND_MESSAGE_URL).mock(return_value=_ok_response())

        async with TelegramClient(token=FAKE_TOKEN) as tg:
            with patch("asyncio.sleep"):
                await tg.send_listings_batch(
                    chat_id="@testchannel",
                    properties=[_make_stored_property(111)],
                )

        import json
        header = json.loads(route.calls[0].request.content)["text"]
        assert "1 new listing" in header
        assert "listings" not in header  # singular
