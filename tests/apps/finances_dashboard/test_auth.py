"""Tests for finances dashboard auth layer and HTTP login flow."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from tempfile import NamedTemporaryFile
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.apps.finances_dashboard.auth import AuthDB, AuthUser
from src.apps.finances_dashboard.config import (
    AccountEntry,
    BrokerEntry,
    DashboardConfig,
    UserEntry,
)
from src.apps.finances_dashboard.run import _build_app, _filter_config


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def auth_db(tmp_path: Path) -> AuthDB:
    db = AuthDB(tmp_path / "auth.db")
    db.create_user("alice", "secret123", [])           # unrestricted
    db.create_user("bob", "pass456", ["ACC-A"])        # restricted to ACC-A
    return db


_FULL_CONFIG = DashboardConfig(
    users=[
        UserEntry(
            name="Oliver",
            brokers=[
                BrokerEntry(
                    name="IBKR",
                    accounts=[
                        AccountEntry(id="ACC-A", label="ISA"),
                        AccountEntry(id="ACC-B", label="GIA"),
                    ],
                ),
            ],
        ),
    ]
)

_MINIMAL_XML = """<?xml version="1.0"?>
<FlexQueryResponse>
  <FlexStatements count="1">
    <FlexStatement accountId="ACC-A" currency="GBP">
      <OpenPositions></OpenPositions>
      <Trades></Trades>
      <CashReport>
        <CashReportCurrency currency="GBP" endingCash="0"/>
      </CashReport>
    </FlexStatement>
  </FlexStatements>
</FlexQueryResponse>
"""


@pytest.fixture
def xml_file(tmp_path: Path) -> Path:
    p = tmp_path / "ibkr.xml"
    p.write_text(_MINIMAL_XML)
    return p


# ---------------------------------------------------------------------------
# AuthDB unit tests
# ---------------------------------------------------------------------------

class TestAuthDB:
    def test_verify_correct_password(self, auth_db: AuthDB) -> None:
        user = auth_db.verify("alice", "secret123")
        assert user is not None
        assert user.username == "alice"
        assert user.sees_all

    def test_verify_wrong_password_returns_none(self, auth_db: AuthDB) -> None:
        assert auth_db.verify("alice", "wrong") is None

    def test_verify_unknown_user_returns_none(self, auth_db: AuthDB) -> None:
        assert auth_db.verify("nobody", "whatever") is None

    def test_verify_restricted_user(self, auth_db: AuthDB) -> None:
        user = auth_db.verify("bob", "pass456")
        assert user is not None
        assert user.permitted_accounts == frozenset({"ACC-A"})
        assert not user.sees_all

    def test_get_user_by_name(self, auth_db: AuthDB) -> None:
        user = auth_db.get_user("alice")
        assert user is not None
        assert user.username == "alice"

    def test_get_user_unknown_returns_none(self, auth_db: AuthDB) -> None:
        assert auth_db.get_user("ghost") is None

    def test_session_secret_stable(self, auth_db: AuthDB) -> None:
        s1 = auth_db.session_secret()
        s2 = auth_db.session_secret()
        assert s1 == s2
        assert len(s1) == 64  # 32 bytes hex

    def test_create_user_idempotent(self, auth_db: AuthDB) -> None:
        auth_db.create_user("alice", "newpassword", [])
        user = auth_db.verify("alice", "newpassword")
        assert user is not None
        assert auth_db.verify("alice", "secret123") is None  # old password gone


# ---------------------------------------------------------------------------
# _filter_config unit tests
# ---------------------------------------------------------------------------

class TestFilterConfig:
    def test_empty_permitted_returns_full_config(self) -> None:
        result = _filter_config(_FULL_CONFIG, frozenset())
        assert result.all_account_ids() == ["ACC-A", "ACC-B"]

    def test_single_account_filtered(self) -> None:
        result = _filter_config(_FULL_CONFIG, frozenset({"ACC-A"}))
        assert result.all_account_ids() == ["ACC-A"]

    def test_unknown_account_excluded(self) -> None:
        result = _filter_config(_FULL_CONFIG, frozenset({"DOES-NOT-EXIST"}))
        assert result.all_account_ids() == []

    def test_both_accounts_permitted(self) -> None:
        result = _filter_config(_FULL_CONFIG, frozenset({"ACC-A", "ACC-B"}))
        assert set(result.all_account_ids()) == {"ACC-A", "ACC-B"}


# ---------------------------------------------------------------------------
# HTTP integration tests (TestClient + mocked XML/yfinance)
# ---------------------------------------------------------------------------

def _make_client(xml_file: Path, auth_db: AuthDB) -> TestClient:
    """Build a TestClient with XML parsing and yfinance mocked out."""
    mock_yf = MagicMock()
    mock_yf.get_close_series.return_value = MagicMock(empty=True)

    with (
        patch("src.apps.finances_dashboard.run.ensure_flex_xml"),
        patch("src.apps.finances_dashboard.run.CachedYFinanceClient", return_value=mock_yf),
        patch("src.apps.finances_dashboard.ajbell_provider.load_ajbell_statements", return_value={}),
        patch("src.apps.finances_dashboard.hl_provider.load_hl_statements", return_value={}),
    ):
        app = _build_app(xml_file, _FULL_CONFIG, trailing_days=5, auth_db=auth_db)

    return TestClient(app, raise_server_exceptions=False, follow_redirects=False)


@pytest.fixture
def client(xml_file: Path, auth_db: AuthDB) -> TestClient:
    return _make_client(xml_file, auth_db)


class TestHTTPAuth:
    def test_login_page_returns_200(self, client: TestClient) -> None:
        r = client.get("/login")
        assert r.status_code == 200
        assert b"<form" in r.content

    def test_unauthenticated_root_redirects_to_login(self, client: TestClient) -> None:
        r = client.get("/")
        assert r.status_code == 302
        assert "/login" in r.headers["location"]

    def test_unauthenticated_account_redirects_to_login(self, client: TestClient) -> None:
        r = client.get("/account/ACC-A")
        assert r.status_code == 302
        assert "/login" in r.headers["location"]

    def test_valid_login_sets_session_and_redirects(self, client: TestClient) -> None:
        r = client.post("/login", data={"username": "alice", "password": "secret123"})
        assert r.status_code == 302
        assert "fd_session" in r.cookies or "session" in r.cookies

    def test_invalid_login_returns_401(self, client: TestClient) -> None:
        r = client.post("/login", data={"username": "alice", "password": "wrong"})
        assert r.status_code == 401

    def test_unknown_user_login_returns_401(self, client: TestClient) -> None:
        r = client.post("/login", data={"username": "ghost", "password": "x"})
        assert r.status_code == 401

    def test_logout_clears_session(self, client: TestClient) -> None:
        client.post("/login", data={"username": "alice", "password": "secret123"})
        r = client.get("/logout")
        # After logout, root should redirect back to login
        r2 = client.get("/")
        assert r2.status_code == 302
        assert "/login" in r2.headers["location"]

    def test_restricted_user_cannot_access_forbidden_account(
        self, client: TestClient
    ) -> None:
        client.post("/login", data={"username": "bob", "password": "pass456"})
        # bob is only permitted ACC-A; ACC-B should be forbidden
        r = client.get("/account/ACC-B")
        # Expect redirect (to permitted account or login) — not 200
        assert r.status_code in (302, 403)

    def test_restricted_user_can_access_permitted_account(
        self, xml_file: Path, auth_db: AuthDB
    ) -> None:
        client = _make_client(xml_file, auth_db)
        client.post("/login", data={"username": "bob", "password": "pass456"})
        r = client.get("/account/ACC-A")
        # May 200 (page rendered) or 302 (redirect to same); must not be 401/403
        assert r.status_code not in (401, 403)
