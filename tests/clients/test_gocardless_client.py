"""Unit tests for the GoCardless Bank Account Data API client.

Verifies that GoCardlessClient correctly handles authentication,
institution listing, requisition management, and data retrieval.
"""

from __future__ import annotations

from unittest.mock import Mock, patch

import httpx

from personal_project.clients.gocardless.client import GoCardlessClient


class TestGoCardlessClient:
    """Tests for GoCardlessClient."""

    @patch("httpx.Client")
    def test_refresh_token_success(self, mock_client_cls: Mock) -> None:
        """Verify that refresh_token updates the access token."""
        mock_client = mock_client_cls.return_value
        mock_resp = Mock()
        mock_resp.json.return_value = {"access": "new_access_token"}
        mock_resp.status_code = 200
        mock_client.post.return_value = mock_resp

        client = GoCardlessClient("secret_id", "secret_key")
        client.refresh_token()

        assert client._access_token == "new_access_token"
        mock_client.post.assert_called_once_with(
            "/token/new/",
            json={"secret_id": "secret_id", "secret_key": "secret_key"},
        )

    @patch("httpx.Client")
    def test_request_auto_refresh_on_401(self, mock_client_cls: Mock) -> None:
        """Verify that the client refreshes the token on a 401 response."""
        mock_client = mock_client_cls.return_value

        # First call fails with 401, second (after refresh) succeeds
        resp_401 = Mock(spec=httpx.Response)
        resp_401.status_code = 401

        resp_200 = Mock(spec=httpx.Response)
        resp_200.status_code = 200
        resp_200.json.return_value = [{"id": "inst_1"}]

        mock_client.request.side_effect = [resp_401, resp_200]

        # Token refresh response
        resp_token = Mock(spec=httpx.Response)
        resp_token.status_code = 200
        resp_token.json.return_value = {"access": "fresh_token"}
        mock_client.post.return_value = resp_token

        client = GoCardlessClient("secret_id", "secret_key")
        client._access_token = "expired_token"

        result = client.list_institutions("GB")

        assert result == [{"id": "inst_1"}]
        assert client._access_token == "fresh_token"
        assert mock_client.request.call_count == 2
        assert mock_client.post.call_count == 1

    @patch("httpx.Client")
    def test_list_institutions(self, mock_client_cls: Mock) -> None:
        """Verify list_institutions calls the correct endpoint."""
        mock_client = mock_client_cls.return_value
        mock_resp = Mock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_resp.json.return_value = [{"id": "BANK1", "name": "Bank 1"}]
        mock_client.request.return_value = mock_resp

        client = GoCardlessClient("secret_id", "secret_key")
        client._access_token = "valid_token"

        result = client.list_institutions("GB")

        assert len(result) == 1
        assert result[0]["id"] == "BANK1"
        mock_client.request.assert_called_with(
            "GET",
            "/institutions/",
            headers={"Authorization": "Bearer valid_token"},
            params={"country": "GB"},
        )

    @patch("httpx.Client")
    def test_create_requisition(self, mock_client_cls: Mock) -> None:
        """Verify create_requisition calls the correct endpoint."""
        mock_client = mock_client_cls.return_value
        mock_resp = Mock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"id": "req1", "link": "https://auth.link"}
        mock_client.request.return_value = mock_resp

        client = GoCardlessClient("secret_id", "secret_key")
        client._access_token = "valid_token"

        result = client.create_requisition("BANK1", "https://redirect", "ref1")

        assert result["id"] == "req1"
        assert result["link"] == "https://auth.link"

    @patch("httpx.Client")
    def test_get_balances(self, mock_client_cls: Mock) -> None:
        """Verify get_balances calls the correct endpoint."""
        mock_client = mock_client_cls.return_value
        mock_resp = Mock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "balances": [{"balanceAmount": {"amount": "100.00"}, "balanceType": "closingBooked"}]
        }
        mock_client.request.return_value = mock_resp

        client = GoCardlessClient("secret_id", "secret_key")
        client._access_token = "valid_token"

        result = client.get_balances("acc1")

        assert len(result) == 1
        assert result[0]["balanceAmount"]["amount"] == "100.00"
