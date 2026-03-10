"""Better.com booking API client package.

Exports the primary client class and credential helper used to authenticate
with and query the Better.com activity booking system.

Typical usage::

    from personal_project.clients.better_com import BetterClient

    client = BetterClient()
    if client.ensure_logged_in():
        slots = client.get_availability(
            "islington-tennis-centre",
            "tennis-court-indoor",
            "2026-03-11",
        )
"""

from .client import BetterClient
from .credentials import KeyringCredentialHelper

__all__ = ["BetterClient", "KeyringCredentialHelper"]
