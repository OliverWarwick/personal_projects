"""GoCardless (Nordigen) Bank Account Data API client.

This module provides a synchronous client for interacting with the GoCardless
Bank Account Data API (formerly Nordigen). It handles authentication,
institution discovery, requisition management, and account data retrieval.

Typical usage:
    client = GoCardlessClient(secret_id="...", secret_key="...")
    institutions = client.list_institutions(country="gb")
    # ... requisition flow ...
    balances = client.get_balances(account_id="...")
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_BASE_URL = "https://bankaccountdata.gocardless.com/api/v2"


class GoCardlessClient:
    """Synchronous client for the GoCardless Bank Account Data API.

    Attributes:
        secret_id: The secret ID provided by GoCardless.
        secret_key: The secret key provided by GoCardless.

    """

    def __init__(self, secret_id: str, secret_key: str) -> None:
        """Initialise the client with credentials.

        Args:
            secret_id: GoCardless API secret ID.
            secret_key: GoCardless API secret key.

        """
        self.secret_id = secret_id
        self.secret_key = secret_key
        self._access_token: str | None = None
        self._client = httpx.Client(base_url=_BASE_URL, timeout=30.0)

    def _get_headers(self) -> dict[str, str]:
        """Return headers with the current access token.

        Returns:
            A dictionary of HTTP headers.

        """
        if not self._access_token:
            self.refresh_token()
        return {"Authorization": f"Bearer {self._access_token}"}

    def refresh_token(self) -> None:
        """Obtain a new access token using secret_id and secret_key.

        Raises:
            httpx.HTTPStatusError: If the token request fails.

        """
        logger.debug("Refreshing GoCardless access token")
        resp = self._client.post(
            "/token/new/",
            json={"secret_id": self.secret_id, "secret_key": self.secret_key},
        )
        resp.raise_for_status()
        data = resp.json()
        self._access_token = str(data["access"])

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Perform an authenticated HTTP request.

        Automatically handles token expiration (401) by refreshing once.

        Args:
            method: HTTP method (GET, POST, etc.).
            path: API path relative to base URL.
            **kwargs: Additional arguments for httpx.request.

        Returns:
            The HTTP response.

        Raises:
            httpx.HTTPStatusError: If the request fails after refresh.

        """
        headers = kwargs.pop("headers", {})
        headers.update(self._get_headers())

        resp = self._client.request(method, path, headers=headers, **kwargs)

        if resp.status_code == httpx.codes.UNAUTHORIZED:
            logger.debug("Access token expired, refreshing...")
            self.refresh_token()
            headers.update(self._get_headers())
            resp = self._client.request(method, path, headers=headers, **kwargs)

        resp.raise_for_status()
        return resp

    def list_institutions(self, country: str) -> list[dict[str, Any]]:
        """List supported financial institutions for a country.

        Args:
            country: Two-letter country code (e.g., "GB", "DE").

        Returns:
            A list of institution dictionaries.

        """
        resp = self._request("GET", "/institutions/", params={"country": country})
        return list(resp.json())

    def create_requisition(
        self,
        institution_id: str,
        redirect_url: str,
        reference: str,
        agreement_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a new requisition (link to a bank).

        Args:
            institution_id: The ID of the bank to link.
            redirect_url: Where the bank should redirect after auth.
            reference: An internal reference for this requisition.
            agreement_id: Optional ID of a pre-created end-user agreement.

        Returns:
            The created requisition dictionary, containing 'link'.

        """
        payload = {
            "redirect": redirect_url,
            "institution_id": institution_id,
            "reference": reference,
        }
        if agreement_id:
            payload["agreement"] = agreement_id

        resp = self._request("POST", "/requisitions/", json=payload)
        return dict(resp.json())

    def get_requisition(self, requisition_id: str) -> dict[str, Any]:
        """Retrieve details of an existing requisition.

        Args:
            requisition_id: The requisition ID.

        Returns:
            The requisition dictionary, containing authorized 'accounts'.

        """
        resp = self._request("GET", f"/requisitions/{requisition_id}/")
        return dict(resp.json())

    def delete_requisition(self, requisition_id: str) -> None:
        """Delete a requisition and its associated access.

        Args:
            requisition_id: The requisition ID.

        """
        self._request("DELETE", f"/requisitions/{requisition_id}/")

    def get_account_metadata(self, account_id: str) -> dict[str, Any]:
        """Retrieve metadata for a specific account.

        Args:
            account_id: The account ID.

        Returns:
            The account metadata dictionary (IBAN, owner name, etc.).

        """
        resp = self._request("GET", f"/accounts/{account_id}/")
        return dict(resp.json())

    def get_balances(self, account_id: str) -> list[dict[str, Any]]:
        """Retrieve balances for a specific account.

        Args:
            account_id: The account ID.

        Returns:
            A list of balance dictionaries.

        """
        resp = self._request("GET", f"/accounts/{account_id}/balances/")
        return list(resp.json().get("balances", []))

    def get_transactions(
        self,
        account_id: str,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> dict[str, Any]:
        """Retrieve transactions for a specific account.

        Args:
            account_id: The account ID.
            date_from: Optional start date (YYYY-MM-DD).
            date_to: Optional end date (YYYY-MM-DD).

        Returns:
            A dictionary containing 'booked' and 'pending' transactions.

        """
        params = {}
        if date_from:
            params["date_from"] = date_from
        if date_to:
            params["date_to"] = date_to

        resp = self._request("GET", f"/accounts/{account_id}/transactions/", params=params)
        return dict(resp.json().get("transactions", {}))

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._client.close()
