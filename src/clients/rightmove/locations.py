"""
Helpers for resolving Rightmove location identifiers.

Rightmove uses opaque identifiers in the format "TYPE^ID", e.g.:
  REGION^87498  - a broad region
  STATION^2     - near a tube/rail station
  POSTCODE^123  - postcode sector

The autocomplete endpoint can be used to look up identifiers by name.
"""

import httpx

AUTOCOMPLETE_URL = "https://www.rightmove.co.uk/property-to-rent/search.html"
TYPEAHEAD_URL = "https://www.rightmove.co.uk/typeAhead/uknoauth"


async def search_location(query: str, client: httpx.AsyncClient) -> list[dict]:
    """
    Returns a list of location suggestions for a given search string.
    Each result has 'locationIdentifier' and 'displayName'.
    """
    params = {
        "typeAheadLocations": query,
    }
    response = await client.get(TYPEAHEAD_URL, params=params)
    response.raise_for_status()
    data = response.json()
    return data.get("typeAheadLocations", [])
