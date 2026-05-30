"""Service layer for the education_funding application.

Provides the core logic for pulling, storing, and querying education and
training funding data (e.g. pilot training costs and cadet pay scales).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.apps.education_funding.config import EducationFundingConfig

logger = logging.getLogger(__name__)


class EducationFundingService:
    """Service for managing education funding research data.

    Orchestrates data retrieval and persistence for training costs and
    pay-scale information across different providers and countries.
    """

    def __init__(self, config: EducationFundingConfig) -> None:
        """Initialise the service.

        Args:
            config: EducationFundingConfig instance holding connection settings.

        """
        self.config = config
