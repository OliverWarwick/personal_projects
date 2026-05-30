"""
Tests for the Rightmove scraper client.

Covers the search and property detail flows using the search config from
src/config/rightmove_ow.yaml (REGION^96855, max £3000, max 2 beds).

HTTP calls are intercepted by respx so no real network requests are made.
The warmup request to the Rightmove homepage is mocked alongside each test.
"""

import json
from pathlib import Path

import httpx
import pytest
import respx

from src.apps.rightmove_scrapper.config import AppConfig, load_config
from src.clients.rightmove import RightmoveClient
from src.clients.rightmove.models import (
    AddedToSite,
    DontShow,
    FurnishType,
    LetType,
    MustHave,
    PropertyDetail,
    PropertySummary,
    PropertyType,
    SearchResults,
    SortType,
)
from src.clients.rightmove.property import PROPERTY_URL
from src.clients.rightmove.search import SEARCH_URLS, WARMUP_URL

from .fixtures import PROPERTY_PAGE_MODEL, make_search_html

CONFIG_PATH = Path(__file__).parents[3] / "src" / "config" / "rightmove_ow.yaml"
RENT_SEARCH_URL = SEARCH_URLS["RENT"]
BUY_SEARCH_URL = SEARCH_URLS["BUY"]


def _mock_warmup() -> None:
    """Register a mock for the session warm-up homepage request."""
    respx.get(WARMUP_URL).mock(return_value=httpx.Response(200, text="<html></html>"))


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------


class TestConfig:
    def test_loads_config(self):
        config = load_config(CONFIG_PATH)
        assert isinstance(config, AppConfig)

    def test_regions_defined(self):
        config = load_config(CONFIG_PATH)
        assert "wandsworth" in config.regions
        assert config.regions["wandsworth"] == "REGION^96855"

    def test_searches_resolve_location_identifiers(self):
        config = load_config(CONFIG_PATH)
        for search in config.searches:
            assert search.location_identifier, (
                f"Search '{search.name}' has no resolved location_identifier"
            )

    def test_wandsworth_search_params(self):
        config = load_config(CONFIG_PATH)
        wandsworth = next(s for s in config.searches if s.region == "wandsworth")
        assert wandsworth.channel == "RENT"
        assert wandsworth.max_price == 3000
        assert wandsworth.min_price == 1500
        assert wandsworth.max_bedrooms == 2
        assert wandsworth.location_identifier == "REGION^96855"

    def test_wandsworth_property_types(self):
        config = load_config(CONFIG_PATH)
        wandsworth = next(s for s in config.searches if s.region == "wandsworth")
        assert PropertyType.FLAT in wandsworth.property_types
        assert PropertyType.TERRACED in wandsworth.property_types

    def test_wandsworth_furnish_types(self):
        config = load_config(CONFIG_PATH)
        wandsworth = next(s for s in config.searches if s.region == "wandsworth")
        assert FurnishType.FURNISHED in wandsworth.furnish_types
        assert FurnishType.PART_FURNISHED in wandsworth.furnish_types

    def test_wandsworth_dont_show(self):
        config = load_config(CONFIG_PATH)
        wandsworth = next(s for s in config.searches if s.region == "wandsworth")
        assert DontShow.RETIREMENT in wandsworth.dont_show
        assert DontShow.SHARED_OWNERSHIP in wandsworth.dont_show

    def test_unknown_region_raises(self):
        with pytest.raises(ValueError, match="unknown region"):
            AppConfig.model_validate({
                "regions": {"wandsworth": "REGION^96855"},
                "searches": [{"name": "test", "region": "nonexistent"}],
            })


# ---------------------------------------------------------------------------
# Search tests — core behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestSearch:
    @respx.mock
    async def test_search_calls_correct_endpoint(self):
        _mock_warmup()
        route = respx.get(RENT_SEARCH_URL).mock(
            return_value=httpx.Response(200, text=make_search_html())
        )

        async with RightmoveClient(request_delay=0) as client:
            await client.search(
                location_identifier="REGION^96855",
                channel="RENT",
                min_price=1500,
                max_price=3000,
                max_bedrooms=2,
                sort_type=SortType.MOST_RECENT,
            )

        assert route.called
        params = dict(httpx.URL(route.calls[0].request.url).params)
        assert params["locationIdentifier"] == "REGION^96855"
        assert params["minPrice"] == "1500"
        assert params["maxPrice"] == "3000"
        assert params["maxBedrooms"] == "2"
        assert params["sortType"] == "6"

    @respx.mock
    async def test_rent_uses_rent_url(self):
        _mock_warmup()
        route = respx.get(RENT_SEARCH_URL).mock(
            return_value=httpx.Response(200, text=make_search_html())
        )
        async with RightmoveClient(request_delay=0) as client:
            await client.search(location_identifier="REGION^96855", channel="RENT")
        assert route.called

    @respx.mock
    async def test_buy_uses_buy_url(self):
        _mock_warmup()
        route = respx.get(BUY_SEARCH_URL).mock(
            return_value=httpx.Response(200, text=make_search_html())
        )
        async with RightmoveClient(request_delay=0) as client:
            await client.search(location_identifier="REGION^96855", channel="BUY")
        assert route.called

    @respx.mock
    async def test_search_returns_search_results(self):
        _mock_warmup()
        respx.get(RENT_SEARCH_URL).mock(
            return_value=httpx.Response(200, text=make_search_html(result_count=247))
        )

        async with RightmoveClient(request_delay=0) as client:
            results = await client.search(location_identifier="REGION^96855")

        assert isinstance(results, SearchResults)
        assert results.total_results == 247
        assert len(results.properties) == 3

    @respx.mock
    async def test_search_parses_property_fields(self):
        _mock_warmup()
        respx.get(RENT_SEARCH_URL).mock(
            return_value=httpx.Response(200, text=make_search_html())
        )

        async with RightmoveClient(request_delay=0) as client:
            results = await client.search(location_identifier="REGION^96855")

        first = results.properties[0]
        assert isinstance(first, PropertySummary)
        assert first.id == 111111111
        assert first.bedrooms == 2
        assert first.address.display_address == "Wandsworth High Street, Wandsworth, SW18"
        assert first.price.amount == 2200
        assert first.price.currency_code == "GBP"
        assert first.price.frequency == "monthly"
        assert first.property_type == "Flat"
        assert first.rightmove_url == "https://www.rightmove.co.uk/properties/111111111"

    @respx.mock
    async def test_search_with_config_wandsworth(self):
        """End-to-end: load config and run the Wandsworth search."""
        _mock_warmup()
        respx.get(RENT_SEARCH_URL).mock(
            return_value=httpx.Response(200, text=make_search_html(result_count=50))
        )

        config = load_config(CONFIG_PATH)
        s = next(sc for sc in config.searches if sc.region == "wandsworth")

        async with RightmoveClient(request_delay=0) as client:
            results = await client.search(
                location_identifier=s.location_identifier,
                channel=s.channel,
                min_price=s.min_price,
                max_price=s.max_price,
                max_bedrooms=s.max_bedrooms,
                sort_type=s.sort_type,
                property_types=s.property_types or None,
                furnish_types=s.furnish_types or None,
                dont_show=s.dont_show or None,
            )

        assert results.total_results > 0

    @respx.mock
    async def test_empty_search_results(self):
        _mock_warmup()
        respx.get(RENT_SEARCH_URL).mock(
            return_value=httpx.Response(200, text=make_search_html(properties=[], result_count=0))
        )

        async with RightmoveClient(request_delay=0) as client:
            results = await client.search(location_identifier="REGION^96855")

        assert results.total_results == 0
        assert results.properties == []

    @respx.mock
    async def test_missing_next_data_raises(self):
        _mock_warmup()
        respx.get(RENT_SEARCH_URL).mock(
            return_value=httpx.Response(200, text="<html><body>No data</body></html>")
        )

        async with RightmoveClient(request_delay=0) as client:
            with pytest.raises(ValueError, match="__NEXT_DATA__"):
                await client.search(location_identifier="REGION^96855")


# ---------------------------------------------------------------------------
# Search tests — filter parameters
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestSearchFilters:
    @respx.mock
    async def test_property_types_sent_as_csv(self):
        _mock_warmup()
        route = respx.get(RENT_SEARCH_URL).mock(
            return_value=httpx.Response(200, text=make_search_html())
        )

        async with RightmoveClient(request_delay=0) as client:
            await client.search(
                location_identifier="REGION^96855",
                property_types=[PropertyType.FLAT, PropertyType.TERRACED],
            )

        params = dict(httpx.URL(route.calls[0].request.url).params)
        assert params["propertyTypes"] == "flat,terraced"

    @respx.mock
    async def test_furnish_types_sent(self):
        _mock_warmup()
        route = respx.get(RENT_SEARCH_URL).mock(
            return_value=httpx.Response(200, text=make_search_html())
        )

        async with RightmoveClient(request_delay=0) as client:
            await client.search(
                location_identifier="REGION^96855",
                furnish_types=[FurnishType.FURNISHED, FurnishType.PART_FURNISHED],
            )

        params = dict(httpx.URL(route.calls[0].request.url).params)
        assert params["furnishTypes"] == "furnished,partFurnished"

    @respx.mock
    async def test_let_type_sent(self):
        _mock_warmup()
        route = respx.get(RENT_SEARCH_URL).mock(
            return_value=httpx.Response(200, text=make_search_html())
        )

        async with RightmoveClient(request_delay=0) as client:
            await client.search(
                location_identifier="REGION^96855",
                let_type=LetType.LONG_TERM,
            )

        params = dict(httpx.URL(route.calls[0].request.url).params)
        assert params["letType"] == "longTerm"

    @respx.mock
    async def test_added_to_site_sent(self):
        _mock_warmup()
        route = respx.get(RENT_SEARCH_URL).mock(
            return_value=httpx.Response(200, text=make_search_html())
        )

        async with RightmoveClient(request_delay=0) as client:
            await client.search(
                location_identifier="REGION^96855",
                added_to_site=AddedToSite.LAST_7_DAYS,
            )

        params = dict(httpx.URL(route.calls[0].request.url).params)
        assert params["addedToSite"] == "last7Days"

    @respx.mock
    async def test_must_have_sent(self):
        _mock_warmup()
        route = respx.get(RENT_SEARCH_URL).mock(
            return_value=httpx.Response(200, text=make_search_html())
        )

        async with RightmoveClient(request_delay=0) as client:
            await client.search(
                location_identifier="REGION^96855",
                must_have=[MustHave.GARDEN, MustHave.PARKING],
            )

        params = dict(httpx.URL(route.calls[0].request.url).params)
        assert params["mustHave"] == "garden,parking"

    @respx.mock
    async def test_dont_show_sent(self):
        _mock_warmup()
        route = respx.get(RENT_SEARCH_URL).mock(
            return_value=httpx.Response(200, text=make_search_html())
        )

        async with RightmoveClient(request_delay=0) as client:
            await client.search(
                location_identifier="REGION^96855",
                dont_show=[DontShow.RETIREMENT, DontShow.SHARED_OWNERSHIP],
            )

        params = dict(httpx.URL(route.calls[0].request.url).params)
        assert params["dontShow"] == "retirement,sharedOwnership"

    @respx.mock
    async def test_floor_area_sent(self):
        _mock_warmup()
        route = respx.get(RENT_SEARCH_URL).mock(
            return_value=httpx.Response(200, text=make_search_html())
        )

        async with RightmoveClient(request_delay=0) as client:
            await client.search(
                location_identifier="REGION^96855",
                min_area_size=500,
                max_area_size=1000,
                area_size_unit="sqft",
            )

        params = dict(httpx.URL(route.calls[0].request.url).params)
        assert params["minAreaSize"] == "500"
        assert params["maxAreaSize"] == "1000"
        assert params["areaSizeUnit"] == "sqft"

    @respx.mock
    async def test_include_sstc_sent(self):
        _mock_warmup()
        route = respx.get(BUY_SEARCH_URL).mock(
            return_value=httpx.Response(200, text=make_search_html())
        )

        async with RightmoveClient(request_delay=0) as client:
            await client.search(
                location_identifier="REGION^96855",
                channel="BUY",
                include_sstc=True,
            )

        params = dict(httpx.URL(route.calls[0].request.url).params)
        assert params["includeSSTC"] == "true"

    @respx.mock
    async def test_new_homes_only_sent(self):
        _mock_warmup()
        route = respx.get(RENT_SEARCH_URL).mock(
            return_value=httpx.Response(200, text=make_search_html())
        )

        async with RightmoveClient(request_delay=0) as client:
            await client.search(
                location_identifier="REGION^96855",
                new_homes_only=True,
            )

        params = dict(httpx.URL(route.calls[0].request.url).params)
        assert params["newHomes"] == "true"

    @respx.mock
    async def test_optional_filters_not_sent_when_none(self):
        """Filters with None/empty values should not appear in the request."""
        _mock_warmup()
        route = respx.get(RENT_SEARCH_URL).mock(
            return_value=httpx.Response(200, text=make_search_html())
        )

        async with RightmoveClient(request_delay=0) as client:
            await client.search(location_identifier="REGION^96855")

        params = dict(httpx.URL(route.calls[0].request.url).params)
        for key in (
            "propertyTypes", "furnishTypes", "letType", "mustHave",
            "dontShow", "addedToSite", "includeSSTC", "newHomes",
            "minAreaSize", "maxAreaSize",
        ):
            assert key not in params, f"Unexpected param in request: {key}"


# ---------------------------------------------------------------------------
# Property detail tests
# ---------------------------------------------------------------------------


def _make_property_html(page_model: dict) -> str:
    """Wrap a PAGE_MODEL dict in the minimal HTML that the scraper expects."""
    model_json = json.dumps(page_model)
    return f"<html><body><script>window.PAGE_MODEL = {model_json};</script></body></html>"


@pytest.mark.asyncio
class TestPropertyDetail:
    @respx.mock
    async def test_fetches_property_detail(self):
        _mock_warmup()
        property_id = 111111111
        url = PROPERTY_URL.format(property_id=property_id)
        respx.get(url).mock(
            return_value=httpx.Response(200, text=_make_property_html(PROPERTY_PAGE_MODEL))
        )

        async with RightmoveClient(request_delay=0) as client:
            detail = await client.get_property(property_id)

        assert isinstance(detail, PropertyDetail)
        assert detail.id == property_id
        assert detail.bedrooms == 2
        assert detail.address.display_address == "Wandsworth High Street, Wandsworth, SW18"
        assert detail.price.amount == 2200
        assert detail.price.frequency == "monthly"

    @respx.mock
    async def test_property_detail_fields(self):
        _mock_warmup()
        property_id = 111111111
        url = PROPERTY_URL.format(property_id=property_id)
        respx.get(url).mock(
            return_value=httpx.Response(200, text=_make_property_html(PROPERTY_PAGE_MODEL))
        )

        async with RightmoveClient(request_delay=0) as client:
            detail = await client.get_property(property_id)

        assert "Wandsworth High Street" in detail.description
        assert "2 double bedrooms" in detail.key_features
        assert len(detail.image_urls) == 2
        assert len(detail.floorplan_urls) == 1
        assert detail.latitude == pytest.approx(51.4567)
        assert detail.longitude == pytest.approx(-0.1921)
        assert detail.size_sq_ft == 750
        assert detail.agent_name == "Example Lettings Agency"

    @respx.mock
    async def test_missing_page_model_raises(self):
        _mock_warmup()
        property_id = 999999999
        url = PROPERTY_URL.format(property_id=property_id)
        respx.get(url).mock(
            return_value=httpx.Response(200, text="<html><body>No data here</body></html>")
        )

        async with RightmoveClient(request_delay=0) as client:
            with pytest.raises(ValueError, match="PAGE_MODEL"):
                await client.get_property(property_id)
