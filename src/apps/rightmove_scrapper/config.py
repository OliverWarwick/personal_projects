"""
Loads and validates the YAML config file for the rightmove scrapper.
"""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, model_validator

from src.clients.rightmove.models import (
    AddedToSite,
    DontShow,
    FurnishType,
    LetType,
    MustHave,
    PropertyType,
    SortType,
)


class SearchConfig(BaseModel):
    name: str
    region: str
    channel: Literal["RENT", "BUY"] = "RENT"

    # Price
    min_price: int | None = None
    max_price: int | None = None

    # Bedrooms
    min_bedrooms: int | None = None
    max_bedrooms: int | None = None

    # Property type
    property_types: list[PropertyType] = []

    # Floor area
    min_area_size: int | None = None
    max_area_size: int | None = None
    area_size_unit: str = "sqft"

    # Sorting
    sort_type: SortType = SortType.MOST_RECENT

    # Location radius (miles)
    radius: float | None = None

    # Recency
    added_to_site: AddedToSite | None = None

    # RENT-only
    furnish_types: list[FurnishType] = []
    let_type: LetType | None = None

    # Feature filters
    must_have: list[MustHave] = []
    dont_show: list[DontShow] = []

    # BUY-only
    include_sstc: bool | None = None
    new_homes_only: bool | None = None

    # Resolved after regions are substituted in — populated by AppConfig
    location_identifier: str = ""


class AppConfig(BaseModel):
    regions: dict[str, str]
    searches: list[SearchConfig]

    @model_validator(mode="after")
    def resolve_region_aliases(self) -> "AppConfig":
        for search in self.searches:
            if search.region not in self.regions:
                raise ValueError(
                    f"Search '{search.name}' references unknown region '{search.region}'. "
                    f"Available regions: {list(self.regions)}"
                )
            search.location_identifier = self.regions[search.region]
        return self


def load_config(path: Path | str) -> AppConfig:
    path = Path(path)
    raw = yaml.safe_load(path.read_text())
    return AppConfig.model_validate(raw)
