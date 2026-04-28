"""
Fixture data for Rightmove client tests.

SEARCH_RESPONSE_PROPERTIES: raw property dicts matching the __NEXT_DATA__ shape.
make_search_html(): wraps properties into a full __NEXT_DATA__ HTML page.

Based on the search: REGION^96855, RENT, min £1500, max £3000, max 2 beds.
"""

import json

SEARCH_RESPONSE_PROPERTIES = [
    {
        "id": 111111111,
        "bedrooms": 2,
        "bathrooms": 1,
        "displayAddress": "Wandsworth High Street, Wandsworth, SW18",
        "countryCode": "GB",
        "propertySubType": "Flat",
        "propertyTypeFullDescription": "Flat / Apartment",
        "summary": "A bright 2 bedroom flat in the heart of Wandsworth.",
        "addedOrReduced": "Added today",
        "letAvailableDate": "Now",
        "price": {
            "amount": 2200,
            "currencyCode": "GBP",
            "frequency": "monthly",
        },
        "propertyImages": {
            "mainImageSrc": "https://media.rightmove.co.uk/dir/crop/10:9-16:9/111111111/photo1.jpg",
        },
    },
    {
        "id": 222222222,
        "bedrooms": 1,
        "bathrooms": 1,
        "displayAddress": "Ram Street, Wandsworth, SW18",
        "countryCode": "GB",
        "propertySubType": "Flat",
        "propertyTypeFullDescription": "Flat / Apartment",
        "summary": "Modern 1 bedroom flat close to Wandsworth Town station.",
        "addedOrReduced": "Added yesterday",
        "letAvailableDate": "Now",
        "price": {
            "amount": 1750,
            "currencyCode": "GBP",
            "frequency": "monthly",
        },
        "propertyImages": {
            "mainImageSrc": "https://media.rightmove.co.uk/dir/crop/10:9-16:9/222222222/photo1.jpg",
        },
    },
    {
        "id": 333333333,
        "bedrooms": 2,
        "bathrooms": 2,
        "displayAddress": "Point Pleasant, Putney, SW15",
        "countryCode": "GB",
        "propertySubType": "Flat",
        "propertyTypeFullDescription": "Flat / Apartment",
        "summary": "Spacious 2 bed 2 bath riverside apartment.",
        "addedOrReduced": "Reduced today",
        "letAvailableDate": "01/05/2026",
        "price": {
            "amount": 2950,
            "currencyCode": "GBP",
            "frequency": "monthly",
        },
        "propertyImages": {
            "mainImageSrc": "https://media.rightmove.co.uk/dir/crop/10:9-16:9/333333333/photo1.jpg",
        },
    },
]


def make_search_html(
    properties: list | None = None,
    result_count: int | None = None,
) -> str:
    """Wrap property dicts into a __NEXT_DATA__ HTML page matching Rightmove's structure."""
    props = properties if properties is not None else SEARCH_RESPONSE_PROPERTIES
    count = result_count if result_count is not None else len(props)
    next_data = {
        "props": {
            "pageProps": {
                "searchResults": {
                    "properties": props,
                    "resultCount": count,
                    "pagination": {
                        "total": 1,
                        "options": [{"value": "0", "description": "1"}],
                        "first": "0",
                        "last": "0",
                        "page": "1",
                    },
                }
            }
        }
    }
    blob = json.dumps(next_data)
    return f'<html><body><script id="__NEXT_DATA__" type="application/json">{blob}</script></body></html>'


PROPERTY_PAGE_MODEL = {
    "propertyData": {
        "id": 111111111,
        "bedrooms": 2,
        "bathrooms": 1,
        "propertySubType": "Flat",
        "address": {
            "displayAddress": "Wandsworth High Street, Wandsworth, SW18",
            "countryCode": "GB",
        },
        "prices": {
            "primaryPrice": "£2,200 pcm",
        },
        "text": {
            "description": (
                "A bright and airy 2 bedroom flat located on Wandsworth High Street. "
                "The property benefits from a modern kitchen, open plan living/dining room, "
                "two double bedrooms and a family bathroom. Available immediately."
            ),
        },
        "keyFeatures": [
            "2 double bedrooms",
            "Modern kitchen",
            "Open plan living/dining",
            "Close to Wandsworth Town station",
            "Available now",
        ],
        "images": [
            {"url": "https://media.rightmove.co.uk/dir/crop/10:9-16:9/111111111/photo1.jpg"},
            {"url": "https://media.rightmove.co.uk/dir/crop/10:9-16:9/111111111/photo2.jpg"},
        ],
        "floorplans": [
            {"url": "https://media.rightmove.co.uk/dir/crop/10:9-16:9/111111111/fp1.jpg"},
        ],
        "location": {
            "latitude": 51.4567,
            "longitude": -0.1921,
        },
        "sizings": [
            {"minimumSize": 750, "maximumSize": 750, "unit": "sqft"},
        ],
        "customer": {
            "brandPlusDisplayName": "Example Lettings Agency",
            "telephone": "020 7000 0000",
        },
    }
}
