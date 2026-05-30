"""IBKR Flex Web Service client package.

This package exposes a synchronous wrapper around the third-party ``ibflex``
library for downloading and parsing Interactive Brokers Flex statements via
the Flex Web Service. The wrapper hides the two-step request/poll flow used
by the Flex Web Service behind a small, typed surface.
"""

from __future__ import annotations

from src.clients.ibkr.client import IBKRFlexClient

__all__ = ["IBKRFlexClient"]
