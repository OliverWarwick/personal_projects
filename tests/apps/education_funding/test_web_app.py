"""Tests for the education-funding FastAPI web UI.

Covers the public landing page, the credential-gated login flow, and the
role-protected dashboards. All requests run through ``TestClient`` so no
real network or browser is involved.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

from personal_project.apps.education_funding.web import app as app_module
from personal_project.apps.education_funding.web.app import create_app
from personal_project.apps.education_funding.web.applications import Application

if TYPE_CHECKING:
    from collections.abc import Iterator

VALID_USERNAME = "ow6"
VALID_PASSWORD = "ow6"
HTTP_OK = 200
HTTP_UNAUTHORIZED = 401
HTTP_SEE_OTHER = 303


@pytest.fixture
def client() -> Iterator[TestClient]:
    """Yield a ``TestClient`` bound to a fresh app instance per test.

    Each test gets its own session-cookie namespace so flows do not leak
    state between tests.
    """
    app = create_app()
    with TestClient(app) as test_client:
        yield test_client


def _login(client: TestClient, role: str) -> None:
    """Sign in with the hardcoded MVP credentials for ``role``."""
    resp = client.post(
        "/login",
        data={"role": role, "username": VALID_USERNAME, "password": VALID_PASSWORD},
        follow_redirects=False,
    )
    assert resp.status_code == HTTP_SEE_OTHER
    assert resp.headers["location"] == f"/{role}"


class TestLandingPage:
    """Tests for the public landing page."""

    def test_landing_renders_both_role_options(self, client: TestClient) -> None:
        """The landing page shows both Investor and Client cards."""
        resp = client.get("/")

        assert resp.status_code == HTTP_OK
        assert "Investor" in resp.text
        assert "Client" in resp.text
        assert 'href="/login/investor"' in resp.text
        assert 'href="/login/client"' in resp.text

    def test_landing_does_not_show_user_chrome(self, client: TestClient) -> None:
        """The landing page hides the signed-in header for anonymous visitors."""
        resp = client.get("/")

        assert "Sign out" not in resp.text


class TestLoginFlow:
    """Tests for the credential-gated login flow."""

    def test_login_form_renders_for_known_role(self, client: TestClient) -> None:
        """The login form surfaces the chosen role to the user."""
        resp = client.get("/login/investor")

        assert resp.status_code == HTTP_OK
        assert "investor" in resp.text.lower()
        assert 'name="username"' in resp.text
        assert 'name="password"' in resp.text

    def test_login_form_rejects_unknown_role(self, client: TestClient) -> None:
        """Unknown roles bounce back to the landing page."""
        resp = client.get("/login/wizard", follow_redirects=False)

        assert resp.status_code == HTTP_SEE_OTHER
        assert resp.headers["location"] == "/"

    def test_valid_credentials_redirect_to_dashboard(self, client: TestClient) -> None:
        """Valid credentials set the session and redirect to the role dashboard."""
        resp = client.post(
            "/login",
            data={
                "role": "investor",
                "username": VALID_USERNAME,
                "password": VALID_PASSWORD,
            },
            follow_redirects=False,
        )

        assert resp.status_code == HTTP_SEE_OTHER
        assert resp.headers["location"] == "/investor"

    def test_tom2_credentials_also_valid(self, client: TestClient) -> None:
        """The second seeded user (``tom2``) can also sign in."""
        resp = client.post(
            "/login",
            data={
                "role": "client",
                "username": "tom2",
                "password": "tom2",
            },
            follow_redirects=False,
        )

        assert resp.status_code == HTTP_SEE_OTHER
        assert resp.headers["location"] == "/client"

    def test_invalid_credentials_show_inline_error(self, client: TestClient) -> None:
        """Wrong credentials re-render the form with a 401 and an error message."""
        resp = client.post(
            "/login",
            data={
                "role": "investor",
                "username": VALID_USERNAME,
                "password": "not-the-password",
            },
        )

        assert resp.status_code == HTTP_UNAUTHORIZED
        assert "Incorrect" in resp.text

    def test_tampered_role_on_post_redirects_home(self, client: TestClient) -> None:
        """A tampered role on the form post bounces to the landing page."""
        resp = client.post(
            "/login",
            data={
                "role": "admin",
                "username": VALID_USERNAME,
                "password": VALID_PASSWORD,
            },
            follow_redirects=False,
        )

        assert resp.status_code == HTTP_SEE_OTHER
        assert resp.headers["location"] == "/"


class TestDashboardAuth:
    """Tests for the role-protected dashboards."""

    def test_unauthenticated_dashboard_redirects(self, client: TestClient) -> None:
        """Hitting a dashboard without a session bounces to ``/``."""
        resp = client.get("/investor", follow_redirects=False)

        assert resp.status_code == HTTP_SEE_OTHER
        assert resp.headers["location"] == "/"

    def test_authenticated_dashboard_shows_username(self, client: TestClient) -> None:
        """A signed-in user sees their username in the header chrome."""
        _login(client, "investor")

        resp = client.get("/investor")

        assert resp.status_code == HTTP_OK
        assert VALID_USERNAME in resp.text
        assert "Sign out" in resp.text

    def test_role_mismatch_redirects(self, client: TestClient) -> None:
        """A signed-in investor cannot view the client dashboard."""
        _login(client, "investor")

        resp = client.get("/client", follow_redirects=False)

        assert resp.status_code == HTTP_SEE_OTHER
        assert resp.headers["location"] == "/"

    def test_logout_clears_session(self, client: TestClient) -> None:
        """Logging out invalidates dashboard access on subsequent requests."""
        _login(client, "investor")
        logout = client.post("/logout", follow_redirects=False)
        assert logout.status_code == HTTP_SEE_OTHER

        resp = client.get("/investor", follow_redirects=False)

        assert resp.status_code == HTTP_SEE_OTHER
        assert resp.headers["location"] == "/"


class TestClientDashboard:
    """Tests for the client dashboard's two-state behaviour."""

    def test_no_application_shows_three_intro_cards(self, client: TestClient) -> None:
        """Without an open application, the client sees the three entry cards."""
        _login(client, "client")

        resp = client.get("/client")

        assert resp.status_code == HTTP_OK
        assert "Commercial Pilot Financing Program" in resp.text
        assert "Testimonials" in resp.text
        assert "Application Portal" in resp.text

    def test_avatar_initials_render(self, client: TestClient) -> None:
        """The hero avatar surfaces the user's initials (``OW`` for ``ow6``)."""
        _login(client, "client")

        resp = client.get("/client")

        assert resp.status_code == HTTP_OK
        assert ">OW<" in resp.text

    def test_application_status_banner_renders_when_application_exists(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """With an application open, the dashboard shows the status stepper."""
        fake_application = Application(
            program_slug="commercial-flying-program",
            reference="APP-0001",
            status="submitted",
            programme="easyJet (CAE)",
            last_updated="27 April 2026",
        )

        def fake_lookup(_username: str) -> Application | None:
            return fake_application

        monkeypatch.setattr(app_module, "get_application_for", fake_lookup)

        _login(client, "client")
        resp = client.get("/client")

        assert resp.status_code == HTTP_OK
        assert "APP-0001" in resp.text
        assert "Application status" in resp.text
        # Stepper labels are present.
        assert "Saved" in resp.text
        assert "Submitted" in resp.text
        assert "Pending review" in resp.text
        assert "Live" in resp.text


class TestClientProgramPage:
    """Tests for the programme info page nested under a programme slug."""

    PROGRAM_URL = "/client/commercial-flying-program/program"

    def test_renders_for_signed_in_client(self, client: TestClient) -> None:
        """A signed-in client sees the programme info page."""
        _login(client, "client")

        resp = client.get(self.PROGRAM_URL)

        assert resp.status_code == HTTP_OK
        assert "Commercial Flying Programme" in resp.text
        # CTA at the bottom links into the nested application portal.
        assert 'href="/client/commercial-flying-program/application"' in resp.text

    def test_includes_repayment_structure(self, client: TestClient) -> None:
        """The page surfaces the ISA repayment terms reused from the fund detail."""
        _login(client, "client")

        resp = client.get(self.PROGRAM_URL)

        assert resp.status_code == HTTP_OK
        assert "Repayment structure" in resp.text
        assert "Income share rate" in resp.text
        assert "per annum" in resp.text  # exact rate is iterated server-side
        assert "principal" in resp.text  # repayment cap line

    def test_includes_product_comparison(self, client: TestClient) -> None:
        """The page surfaces the comparison vs other financing options."""
        _login(client, "client")

        resp = client.get(self.PROGRAM_URL)

        assert resp.status_code == HTTP_OK
        assert "Compared to other options" in resp.text
        assert "Specialist pilot loan" in resp.text
        assert "Self-funded" in resp.text

    def test_unknown_program_slug_redirects(self, client: TestClient) -> None:
        """Unknown programme slug bounces back to the dashboard."""
        _login(client, "client")

        resp = client.get("/client/nope/program", follow_redirects=False)

        assert resp.status_code == HTTP_SEE_OTHER
        assert resp.headers["location"] == "/client"

    def test_unauthenticated_redirects(self, client: TestClient) -> None:
        """Anonymous visitors are bounced to the landing page."""
        resp = client.get(self.PROGRAM_URL, follow_redirects=False)

        assert resp.status_code == HTTP_SEE_OTHER
        assert resp.headers["location"] == "/"

    def test_investor_role_cannot_view(self, client: TestClient) -> None:
        """A signed-in investor cannot see the client-only programme page."""
        _login(client, "investor")

        resp = client.get(self.PROGRAM_URL, follow_redirects=False)

        assert resp.status_code == HTTP_SEE_OTHER
        assert resp.headers["location"] == "/"


class TestClientTestimonialsPage:
    """Tests for the per-programme testimonials page."""

    TESTIMONIALS_URL = "/client/commercial-flying-program/testimonials"

    def test_renders_three_testimonials(self, client: TestClient) -> None:
        """The page surfaces all three seeded testimonials by name."""
        _login(client, "client")

        resp = client.get(self.TESTIMONIALS_URL)

        assert resp.status_code == HTTP_OK
        for name in ("Hannah Brooke", "Marcus Chen", "Aisha Okonkwo"):
            assert name in resp.text

    def test_unknown_program_slug_redirects(self, client: TestClient) -> None:
        """Unknown programme slug bounces back to the dashboard."""
        _login(client, "client")

        resp = client.get(
            "/client/nope/testimonials",
            follow_redirects=False,
        )

        assert resp.status_code == HTTP_SEE_OTHER
        assert resp.headers["location"] == "/client"


class TestClientApplicationPortal:
    """Tests for the per-programme application portal."""

    APPLICATION_URL = "/client/commercial-flying-program/application"
    SAVE_URL = "/client/commercial-flying-program/application/save"
    SUBMIT_URL = "/client/commercial-flying-program/application/submit"

    def test_renders_with_form_when_no_draft_exists(self, client: TestClient) -> None:
        """First visit creates a draft and renders the editable form."""
        _login(client, "client")

        resp = client.get(self.APPLICATION_URL)

        assert resp.status_code == HTTP_OK
        assert "Your application" in resp.text
        # All six section headings are present
        for heading in (
            "About yourself",
            "Motivation",
            "Experience",
            "Funding",
            "CV",
            "Video",
        ):
            assert heading in resp.text
        # Form action targets
        assert 'action="/client/commercial-flying-program/application/save"' in resp.text

    def test_save_about_section_persists_fields(self, client: TestClient) -> None:
        """POSTing the About section stores the supplied fields on disk."""
        _login(client, "client")

        save = client.post(
            self.SAVE_URL,
            data={
                "section": "about",
                "full_name": "Sam Pilot",
                "date_of_birth": "1998-06-01",
                "location": "Bristol, UK",
                "nationality": "British",
                "personal_statement": "Aspiring commercial pilot.",
                "programme": "easyJet MPL",
            },
            follow_redirects=False,
        )

        assert save.status_code == HTTP_SEE_OTHER

        # Reload the page and confirm field values round-trip.
        page = client.get(self.APPLICATION_URL)
        assert "Sam Pilot" in page.text
        assert "Bristol, UK" in page.text

    def test_save_funding_section_records_amounts(self, client: TestClient) -> None:
        """The funding section stores cost, requested, and explanation."""
        _login(client, "client")

        client.post(
            self.SAVE_URL,
            data={
                "section": "funding",
                "total_program_cost_gbp": "120000",
                "funding_requested_gbp": "90000",
                "self_funded_explanation": "Family loan plus savings.",
            },
            follow_redirects=False,
        )

        page = client.get(self.APPLICATION_URL)
        assert "£120,000" in page.text
        assert "£90,000" in page.text
        # Self-funded gap = 120k - 90k = 30k
        assert "£30,000" in page.text
        assert "Family loan" in page.text

    def test_submit_locks_application_into_read_only_view(
        self,
        client: TestClient,
    ) -> None:
        """Submission flips the page into a read-only summary."""
        _login(client, "client")

        # Save something so submission has content.
        client.post(
            self.SAVE_URL,
            data={"section": "motivation", "motivation": "I want to fly."},
        )
        submit = client.post(self.SUBMIT_URL, follow_redirects=False)

        assert submit.status_code == HTTP_SEE_OTHER
        assert submit.headers["location"] == "/client"

        # Re-rendering shows the read-only header copy and no save buttons.
        page = client.get(self.APPLICATION_URL)
        assert "Your submitted application" in page.text
        assert "Save section" not in page.text

    def test_dashboard_view_application_after_submit(
        self,
        client: TestClient,
    ) -> None:
        """After submission the dashboard CTA reads "View application"."""
        _login(client, "client")
        client.post(
            self.SAVE_URL,
            data={"section": "motivation", "motivation": "I want to fly."},
        )
        client.post(self.SUBMIT_URL)

        resp = client.get("/client")

        assert resp.status_code == HTTP_OK
        assert "View application" in resp.text

    def test_dashboard_continue_application_while_saved(
        self,
        client: TestClient,
    ) -> None:
        """Saving without submitting shows ``Continue application`` on the dashboard."""
        _login(client, "client")
        # Saving any section creates a draft with status=saved.
        client.post(
            self.SAVE_URL,
            data={"section": "motivation", "motivation": "Half-finished."},
        )

        resp = client.get("/client")

        assert resp.status_code == HTTP_OK
        assert "Continue application" in resp.text

    def test_unknown_program_slug_redirects(self, client: TestClient) -> None:
        """Unknown programme slug bounces back to the dashboard."""
        _login(client, "client")

        resp = client.get(
            "/client/nope/application",
            follow_redirects=False,
        )

        assert resp.status_code == HTTP_SEE_OTHER
        assert resp.headers["location"] == "/client"


class TestInvestorDashboard:
    """Tests for the three-card investor dashboard."""

    def test_dashboard_lists_three_options(self, client: TestClient) -> None:
        """The dashboard surfaces Funds, P2P, and Portfolio entry points."""
        _login(client, "investor")

        resp = client.get("/investor")

        assert resp.status_code == HTTP_OK
        assert "Fund Investments" in resp.text
        assert "Peer to Peer Investments" in resp.text
        assert "Portfolio" in resp.text


class TestInvestorPortfolio:
    """Tests for the placeholder investor portfolio page."""

    def test_authenticated_investor_can_view(self, client: TestClient) -> None:
        """A signed-in investor sees the portfolio placeholder page."""
        _login(client, "investor")

        resp = client.get("/investor/portfolio")

        assert resp.status_code == HTTP_OK
        assert "Portfolio" in resp.text

    def test_unauthenticated_redirects(self, client: TestClient) -> None:
        """Anonymous visitors are bounced back to the landing page."""
        resp = client.get("/investor/portfolio", follow_redirects=False)

        assert resp.status_code == HTTP_SEE_OTHER
        assert resp.headers["location"] == "/"

    def test_client_role_cannot_view(self, client: TestClient) -> None:
        """A signed-in client is bounced off the investor portfolio page."""
        _login(client, "client")

        resp = client.get("/investor/portfolio", follow_redirects=False)

        assert resp.status_code == HTTP_SEE_OTHER
        assert resp.headers["location"] == "/"


class TestInvestorOpportunities:
    """Tests for the Investment Opportunities page and its tabs."""

    def test_unauthenticated_redirects(self, client: TestClient) -> None:
        """Anonymous visitors are bounced back to the landing page."""
        resp = client.get("/investor/opportunities", follow_redirects=False)

        assert resp.status_code == HTTP_SEE_OTHER
        assert resp.headers["location"] == "/"

    def test_default_tab_is_funds(self, client: TestClient) -> None:
        """Hitting the page with no ``tab`` query lands on the Funds tab."""
        _login(client, "investor")

        resp = client.get("/investor/opportunities")

        assert resp.status_code == HTTP_OK
        assert "Pilot Training Tranche I" in resp.text

    def test_p2p_tab_renders(self, client: TestClient) -> None:
        """The P2P tab renders without error (content tested elsewhere)."""
        _login(client, "investor")

        resp = client.get("/investor/opportunities?tab=p2p")

        assert resp.status_code == HTTP_OK
        # The tab navigation surfaces both options.
        assert "Funds" in resp.text
        assert "Peer to Peer" in resp.text

    def test_unknown_tab_falls_back_to_funds(self, client: TestClient) -> None:
        """An unrecognised ``tab`` value falls back to Funds rather than 404."""
        _login(client, "investor")

        resp = client.get("/investor/opportunities?tab=banana")

        assert resp.status_code == HTTP_OK
        assert "Pilot Training Tranche I" in resp.text

    def test_client_role_cannot_view_opportunities(self, client: TestClient) -> None:
        """A signed-in client is bounced off the investor opportunities page."""
        _login(client, "client")

        resp = client.get("/investor/opportunities", follow_redirects=False)

        assert resp.status_code == HTTP_SEE_OTHER
        assert resp.headers["location"] == "/"


class TestCadetProfile:
    """Tests for the per-cadet P2P profile detail page."""

    def test_known_slug_renders_profile(self, client: TestClient) -> None:
        """A known cadet slug renders the profile with their name and CV."""
        _login(client, "investor")

        resp = client.get("/investor/opportunities/p2p/sophie-hartley")

        assert resp.status_code == HTTP_OK
        assert "Sophie Hartley" in resp.text
        # Personal Statement block is the first ``###`` heading in every CV.
        assert "Personal Statement" in resp.text
        # Video iframe wired to the placeholder clip.
        assert "youtube.com/embed/" in resp.text

    def test_unknown_slug_redirects_to_p2p_tab(self, client: TestClient) -> None:
        """A bad slug bounces back to the P2P tab rather than 404."""
        _login(client, "investor")

        resp = client.get(
            "/investor/opportunities/p2p/nope",
            follow_redirects=False,
        )

        assert resp.status_code == HTTP_SEE_OTHER
        assert resp.headers["location"] == "/investor/opportunities?tab=p2p"

    def test_unauthenticated_redirects(self, client: TestClient) -> None:
        """Anonymous visitors are bounced back to the landing page."""
        resp = client.get(
            "/investor/opportunities/p2p/sophie-hartley",
            follow_redirects=False,
        )

        assert resp.status_code == HTTP_SEE_OTHER
        assert resp.headers["location"] == "/"

    def test_profile_route_remains_reachable(self, client: TestClient) -> None:
        """Direct cadet profile URLs still resolve (linked from placements)."""
        _login(client, "investor")

        resp = client.get("/investor/opportunities/p2p/james-whitfield")

        assert resp.status_code == HTTP_OK
        assert "James Whitfield" in resp.text

    def test_profile_renders_funding_breakdown_sidebar(
        self, client: TestClient,
    ) -> None:
        """The profile page surfaces the Funding Sought/Secured box and donut."""
        _login(client, "investor")

        resp = client.get("/investor/opportunities/p2p/sophie-hartley")

        assert resp.status_code == HTTP_OK
        assert "Funding Sought" in resp.text
        assert "Funding Secured" in resp.text
        # Donut legend rows are present.
        assert "Secured" in resp.text
        assert "Remaining" in resp.text
        assert "Self" in resp.text
        # SVG donut is wired up.
        assert "<svg" in resp.text
        assert 'pathLength="100"' in resp.text


class TestFundDetail:
    """Tests for the per-fund detail page."""

    def test_known_fund_renders(self, client: TestClient) -> None:
        """The detail page for the seeded fund renders with its objectives."""
        _login(client, "investor")

        resp = client.get("/investor/opportunities/funds/pilot-training-tranche-i")

        assert resp.status_code == HTTP_OK
        assert "Pilot Training Tranche I" in resp.text
        assert "Objectives" in resp.text

    def test_unknown_fund_redirects_to_funds_tab(self, client: TestClient) -> None:
        """A bad slug bounces back to the Funds tab rather than 404."""
        _login(client, "investor")

        resp = client.get(
            "/investor/opportunities/funds/nope",
            follow_redirects=False,
        )

        assert resp.status_code == HTTP_SEE_OTHER
        assert resp.headers["location"] == "/investor/opportunities?tab=funds"


class TestFundPlacements:
    """Tests for the per-fund placements roster page."""

    def test_known_fund_renders_placement_rows(self, client: TestClient) -> None:
        """The placements page lists each placed cadet with their stage."""
        _login(client, "investor")

        resp = client.get(
            "/investor/opportunities/funds/pilot-training-tranche-i/placements",
        )

        assert resp.status_code == HTTP_OK
        assert "Placements" in resp.text
        # At least one of the seeded placed cadets shows up.
        assert "Sophie Hartley" in resp.text
        assert "Phase" in resp.text

    def test_placement_rows_link_to_cadet_profile(self, client: TestClient) -> None:
        """Each placement row links through to the cadet's profile page."""
        _login(client, "investor")

        resp = client.get(
            "/investor/opportunities/funds/pilot-training-tranche-i/placements",
        )

        assert resp.status_code == HTTP_OK
        assert "/investor/opportunities/p2p/sophie-hartley" in resp.text

    def test_unknown_fund_redirects_to_funds_tab(self, client: TestClient) -> None:
        """A bad fund slug bounces back to the Funds tab."""
        _login(client, "investor")

        resp = client.get(
            "/investor/opportunities/funds/nope/placements",
            follow_redirects=False,
        )

        assert resp.status_code == HTTP_SEE_OTHER
        assert resp.headers["location"] == "/investor/opportunities?tab=funds"

    def test_unauthenticated_redirects(self, client: TestClient) -> None:
        """Anonymous visitors are bounced back to the landing page."""
        resp = client.get(
            "/investor/opportunities/funds/pilot-training-tranche-i/placements",
            follow_redirects=False,
        )

        assert resp.status_code == HTTP_SEE_OTHER
        assert resp.headers["location"] == "/"
