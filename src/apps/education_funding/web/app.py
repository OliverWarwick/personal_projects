# pyright: reportUnusedFunction=false
"""FastAPI application for the education-funding web UI.

Defines the app factory, session middleware, and the public/private routes
that make up the MVP flow:

* ``GET  /``                  — landing page with the two role choices.
* ``GET  /login/{role}``      — credential form, with the chosen role carried
                                through as a hidden field.
* ``POST /login``             — validates credentials and sets the session.
* ``POST /logout``            — clears the session.
* ``GET  /investor`` / ``/client`` — blank role dashboards (auth-gated).

The session secret is generated fresh per process, so restarts invalidate
cookies. That is intentional for the MVP — wire in a stable secret when
this graduates beyond a local prototype.

The ``reportUnusedFunction`` pragma above silences pyright on the route
handlers — they are registered via the ``@app.get`` / ``@app.post``
decorators rather than referenced by name, which the analyser cannot see.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, Form, Request, UploadFile, status
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    RedirectResponse,
    Response,
)
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from src.apps.education_funding.config import EducationFundingConfig
from src.apps.education_funding.web.application_store import (
    get_application,
    get_application_for,
    get_or_create_draft,
    get_upload_path,
    store_cv_upload,
    store_proof_of_funds_upload,
    store_video_upload,
    submit_application,
    update_draft,
)
from src.apps.education_funding.web.applications import (
    APPLICATION_STATUS_LABELS,
    APPLICATION_STATUS_ORDER,
)
from src.apps.education_funding.web.auth import (
    VALID_ROLES,
    credentials_are_valid,
    get_session_context,
)
from src.apps.education_funding.web.cadets import get_cadet_profiles
from src.apps.education_funding.web.cv_loader import get_cv_html
from src.apps.education_funding.web.funds import (
    get_fund_by_slug,
    get_open_funds,
)
from src.apps.education_funding.web.p2p_batches import (
    get_batch_by_slug,
    get_open_batches,
)
from src.apps.education_funding.web.placements import (
    get_placements_for_fund,
)
from src.apps.education_funding.web.portfolio_data import get_portfolio
from src.apps.education_funding.web.programs import (
    get_program_by_slug,
    get_programs,
)
from src.apps.education_funding.web.testimonials import get_testimonials
from src.apps.education_funding.web.user_avatar import (
    username_avatar_color_hex,
    username_initials,
)

_OPPORTUNITY_TABS = {"funds", "p2p"}
_DEFAULT_OPPORTUNITY_TAB = "funds"

# Placeholder pilot-career interview clip used on every cadet profile until
# real per-cadet videos exist. ``UgxG9vDK6lY`` is "A perspective on pilot
# careers - Interview with Mentour Pilot and PlaneOldBen".
_PROFILE_VIDEO_ID = "UgxG9vDK6lY"

# Steps shown on the client-facing programme info page. Kept in code rather
# than baked into the template so they're easy to iterate on, and so other
# pages (e.g. the application portal once it lands) can reuse them.
_PROGRAM_STEPS: tuple[dict[str, str], ...] = (
    {
        "title": "Open an application",
        "body": (
            "Start a draft from the application portal. We capture the basics, "
            "your training pathway preference, and any conditional offers in hand."
        ),
    },
    {
        "title": "Submit supporting documents",
        "body": (
            "Class 1 medical, ID, programme offer letter, and a short statement "
            "covering your training plan and timeline."
        ),
    },
    {
        "title": "Review and offer",
        "body": (
            "A single reviewer assesses your file end-to-end. We come back with "
            "an offer detailing the funded amount, repayment share, and term."
        ),
    },
    {
        "title": "Sign and disburse",
        "body": (
            "Once you sign, we release funds directly to the training provider "
            "in line with the programme's payment milestones."
        ),
    },
)

# Headline repayment terms reused on the client programme page. Mirrors
# the Repayment Structure summary on the investor fund detail without the
# illustrative charts.
_REPAYMENT_TERMS: tuple[dict[str, str], ...] = (
    {"label": "Income share rate", "value": "15% per annum"},
    {"label": "Maximum term", "value": "20 years"},
    {"label": "Repayment cap", "value": "3× principal"},
)

# Comparison roster for "this product vs other options". The current
# product is flagged via ``is_current`` so the template can highlight it.
_PRODUCT_COMPARISON: tuple[dict[str, object], ...] = (
    {
        "name": "Commercial Flying Programme",
        "summary": "Income Share Agreement — repay only when you're flying.",
        "is_current": True,
        "rows": (
            ("Upfront cost to you", "Nothing — full training capital advanced"),
            ("Repayment basis", "15% of gross annual base salary, post-qualification"),
            ("Maximum repayment", "Capped at 3× principal advanced"),
            ("Term", "Up to 20 years from first disbursement"),
            ("If you don't qualify or fly", "No salary, no repayment due"),
        ),
    },
    {
        "name": "Specialist pilot loan",
        "summary": "Fixed-rate loan from a sector-specific lender (e.g. Futurity Finance).",
        "is_current": False,
        "rows": (
            ("Upfront cost to you", "Nothing — capital advanced"),
            ("Repayment basis", "Fixed monthly principal + interest, c. 8-12% APR"),
            ("Maximum repayment", "Uncapped — accrues until paid off"),
            ("Term", "5-10 years, repayment starts on drawdown"),
            ("If you don't qualify or fly", "Repayments still due in full"),
        ),
    },
    {
        "name": "Airline-sponsored cadet (BA / Jet2)",
        "summary": "Training paid by the airline against a salary deduction or bond.",
        "is_current": False,
        "rows": (
            ("Upfront cost to you", "Nothing — airline funds training"),
            ("Repayment basis", "Reduced post-qualification salary or fixed bond"),
            ("Maximum repayment", "Implicit — c. £40k below market over 4 years (BA)"),
            ("Term", "Tied to bond length, typically 4-7 years"),
            ("If you don't qualify or fly", "Released — but limited to airline placements"),
        ),
    },
    {
        "name": "Self-funded",
        "summary": "Pay training costs from savings, family, or property-secured borrowing.",
        "is_current": False,
        "rows": (
            ("Upfront cost to you", "Up to £130k from your own resources"),
            ("Repayment basis", "None to a third party (or mortgage interest if secured)"),
            ("Maximum repayment", "None — but capital is at risk"),
            ("Term", "N/A"),
            ("If you don't qualify or fly", "Capital lost; no recovery mechanism"),
        ),
    },
)

# Sections rendered in the application portal sidebar, in display order.
# Each entry pairs the URL fragment / completion-flag key with a human
# label and a one-line hint shown in the section heading.
APPLICATION_SECTIONS: tuple[dict[str, str], ...] = (
    {"key": "about", "label": "About yourself", "hint": "Who you are and where you're based."},
    {"key": "motivation", "label": "Motivation", "hint": "Why a commercial pilot career, and why now."},
    {"key": "experience", "label": "Experience", "hint": "Aviation activities and relevant work history."},
    {"key": "funding", "label": "Funding", "hint": "How much you need and how you'll cover the rest."},
    {"key": "cv", "label": "CV", "hint": "Upload your CV (PDF, DOC, DOCX, max 10 MB)."},
    {"key": "video", "label": "Video", "hint": "Short intro video — file upload or external URL."},
)

# Sections accepted by the save handler — the upload routes handle ``cv``
# directly, so it isn't here. The set keeps the save URL from being
# coerced into mutating arbitrary fields.
_SAVEABLE_SECTIONS: frozenset[str] = frozenset(
    {"about", "motivation", "experience", "funding", "video"},
)

# Maps a downloadable file kind to the ``Application`` attribute that
# stores the filename. Used by the download route to look up the file
# after auth checks.
_DOWNLOADABLE_KINDS: dict[str, str] = {
    "cv": "cv_filename",
    "video": "video_filename",
    "proof_of_funds": "proof_of_funds_filename",
}

_TEMPLATES_DIR = Path(__file__).parent / "templates"
_templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


def _redirect(location: str) -> RedirectResponse:
    """Build a 303 redirect to ``location``.

    303 is the correct status for redirecting after a form POST so that the
    browser switches to GET on the target.
    """
    return RedirectResponse(location, status_code=status.HTTP_303_SEE_OTHER)


def _register_auth_routes(app: FastAPI) -> None:
    """Attach the public landing / login / logout routes to ``app``."""

    @app.get("/", response_class=HTMLResponse)
    async def landing(request: Request) -> Response:
        """Render the public landing page with the role-selection cards."""
        return _templates.TemplateResponse(request, "landing.html", {})

    @app.get("/login/{role}", response_class=HTMLResponse)
    async def login_form(request: Request, role: str) -> Response:
        """Render the login form for the chosen role.

        Unknown roles bounce back to the landing page so that the URL
        space cannot be used to invent new sign-in flows.
        """
        if role not in VALID_ROLES:
            return _redirect("/")
        return _templates.TemplateResponse(
            request,
            "login.html",
            {"role": role, "error": None},
        )

    @app.post("/login")
    async def login_submit(
        request: Request,
        role: Annotated[str, Form()],
        username: Annotated[str, Form()],
        password: Annotated[str, Form()],
    ) -> Response:
        """Validate credentials, set the session, and redirect to the dashboard.

        Re-renders the login form with a 401 and an inline error when the
        credentials are wrong, and bounces to ``/`` when the role on the
        form has been tampered with.
        """
        if role not in VALID_ROLES:
            return _redirect("/")
        if not credentials_are_valid(username, password):
            return _templates.TemplateResponse(
                request,
                "login.html",
                {"role": role, "error": "Incorrect username or password."},
                status_code=status.HTTP_401_UNAUTHORIZED,
            )
        request.session["username"] = username
        request.session["role"] = role
        return _redirect(f"/{role}")

    @app.post("/logout")
    async def logout(request: Request) -> Response:
        """Clear the session cookie and return to the landing page."""
        request.session.clear()
        return _redirect("/")


def _register_investor_routes(app: FastAPI) -> None:
    """Attach the investor dashboard, opportunities, and detail routes to ``app``."""

    @app.get("/investor", response_class=HTMLResponse)
    async def investor_dashboard(request: Request) -> Response:
        """Render the investor dashboard with the three opportunity cards.

        Bounces unauthenticated visitors and signed-in clients back to ``/``.
        """
        ctx = get_session_context(request, expected_role="investor")
        if ctx is None:
            return _redirect("/")
        return _templates.TemplateResponse(
            request,
            "investor_dashboard.html",
            {"role": "investor", "username": ctx["username"]},
        )

    @app.get("/investor/portfolio", response_class=HTMLResponse)
    async def investor_portfolio(request: Request) -> Response:
        """Render the investor portfolio page.

        Passes the seeded portfolio (fund + P2P positions) to the template
        so the allocation chart, summary widgets, and position cards can
        be rendered. Bounces unauthenticated visitors and signed-in clients
        back to ``/``.
        """
        ctx = get_session_context(request, expected_role="investor")
        if ctx is None:
            return _redirect("/")
        portfolio = get_portfolio()
        return _templates.TemplateResponse(
            request,
            "portfolio.html",
            {
                "role": "investor",
                "username": ctx["username"],
                "portfolio": portfolio,
            },
        )

    @app.get("/investor/opportunities", response_class=HTMLResponse)
    async def investor_opportunities(request: Request, tab: str | None = None) -> Response:
        """Render the Investment Opportunities page with Funds / P2P tabs.

        ``tab`` is read from the query string and falls back to ``"funds"``
        when missing or unrecognised, so users always land on a valid view.
        """
        ctx = get_session_context(request, expected_role="investor")
        if ctx is None:
            return _redirect("/")
        active_tab = tab if tab in _OPPORTUNITY_TABS else _DEFAULT_OPPORTUNITY_TAB
        return _templates.TemplateResponse(
            request,
            "opportunities.html",
            {
                "role": "investor",
                "username": ctx["username"],
                "tab": active_tab,
                "funds": get_open_funds(),
                "batches": get_open_batches(),
            },
        )

    @app.get("/investor/opportunities/funds/{slug}", response_class=HTMLResponse)
    async def investor_fund_detail(request: Request, slug: str) -> Response:
        """Render the detail page for a single fund.

        Unknown slugs bounce back to the Funds tab rather than 404 — keeps
        the navigation forgiving while the fund roster is small.
        """
        ctx = get_session_context(request, expected_role="investor")
        if ctx is None:
            return _redirect("/")
        fund = get_fund_by_slug(slug)
        if fund is None:
            return _redirect("/investor/opportunities?tab=funds")
        return _templates.TemplateResponse(
            request,
            "fund_detail.html",
            {
                "role": "investor",
                "username": ctx["username"],
                "fund": fund,
            },
        )

    @app.get(
        "/investor/opportunities/p2p/batches/{slug}",
        response_class=HTMLResponse,
    )
    async def investor_p2p_batch_detail(request: Request, slug: str) -> Response:
        """Render the detail page for a single P2P batch.

        Shows objectives, return projections, widgets, and the cadet roster
        for the batch. Unknown slugs bounce back to the P2P tab.
        """
        ctx = get_session_context(request, expected_role="investor")
        if ctx is None:
            return _redirect("/")
        batch = get_batch_by_slug(slug)
        if batch is None:
            return _redirect("/investor/opportunities?tab=p2p")
        all_cadets = get_cadet_profiles()
        batch_cadets = [c for c in all_cadets if c.slug in batch.cadet_slugs]
        return _templates.TemplateResponse(
            request,
            "p2p_batch_detail.html",
            {
                "role": "investor",
                "username": ctx["username"],
                "batch": batch,
                "cadets": batch_cadets,
            },
        )

    @app.get(
        "/investor/opportunities/p2p/{slug}",
        response_class=HTMLResponse,
    )
    async def investor_cadet_profile(request: Request, slug: str) -> Response:
        """Render the profile detail page for a single P2P cadet.

        Unknown slugs bounce back to the P2P tab so a stale link never
        produces a 404 while the cadet roster is small.
        """
        ctx = get_session_context(request, expected_role="investor")
        if ctx is None:
            return _redirect("/")
        cadet = next(
            (profile for profile in get_cadet_profiles() if profile.slug == slug),
            None,
        )
        if cadet is None:
            return _redirect("/investor/opportunities?tab=p2p")
        return _templates.TemplateResponse(
            request,
            "profile.html",
            {
                "role": "investor",
                "username": ctx["username"],
                "cadet": cadet,
                "video_id": _PROFILE_VIDEO_ID,
                "cv_html": get_cv_html(slug),
            },
        )

    @app.get(
        "/investor/opportunities/funds/{slug}/placements",
        response_class=HTMLResponse,
    )
    async def investor_fund_placements(request: Request, slug: str) -> Response:
        """Render the placements roster for a single fund.

        Shows the cadets the fund has currently backed. Unknown fund slugs
        bounce back to the Funds tab.
        """
        ctx = get_session_context(request, expected_role="investor")
        if ctx is None:
            return _redirect("/")
        fund = get_fund_by_slug(slug)
        if fund is None:
            return _redirect("/investor/opportunities?tab=funds")
        return _templates.TemplateResponse(
            request,
            "placements.html",
            {
                "role": "investor",
                "username": ctx["username"],
                "fund": fund,
                "placements": get_placements_for_fund(slug),
            },
        )


def _client_auth(request: Request, slug: str) -> dict[str, str] | RedirectResponse:
    """Resolve client auth + programme slug to a context dict or redirect.

    Returns the session context (with ``username`` / ``role``) on
    success, or a ``RedirectResponse`` when the visitor is unauthorised
    or the programme slug is unknown. Lets every portal handler keep
    a single ``isinstance(..., RedirectResponse)`` early-return.
    """
    ctx = get_session_context(request, expected_role="client")
    if ctx is None:
        return _redirect("/")
    if get_program_by_slug(slug) is None:
        return _redirect("/client")
    return {"username": ctx["username"], "role": ctx["role"]}


def _form_str(form: Any, key: str) -> str:
    """Pull a string value out of a Starlette ``FormData`` instance.

    Coerces ``None`` and ``UploadFile`` to empty string so the result
    is always safe to feed into a section-field mapping.
    """
    value = form.get(key)
    if isinstance(value, str):
        return value
    return ""


def _form_int(form: Any, key: str) -> int:
    """Parse a form field as a non-negative integer, defaulting to zero."""
    raw = _form_str(form, key)
    try:
        return max(0, int(raw))
    except ValueError:
        return 0


async def _handle_upload(
    request: Request,
    slug: str,
    *,
    upload: UploadFile,
    section: str,
    store: Any,
    field: str,
    saved_marker: str | None = None,
    error_marker: str | None = None,
) -> Response:
    """Run the shared auth + store + redirect flow for the upload routes.

    Auth-checks, ensures a draft exists, calls the supplied ``store``
    helper, and updates the draft with the stored filename. The
    ``saved_marker`` / ``error_marker`` values land on the redirect
    URL's ``?saved=`` query so the template can flash a success or
    failure pill.
    """
    ctx = _client_auth(request, slug)
    if isinstance(ctx, RedirectResponse):
        return ctx
    application = get_or_create_draft(ctx["username"], slug)
    if not application.is_editable:
        return _redirect(f"/client/{slug}/application")
    stored = await store(ctx["username"], slug, upload)
    saved = saved_marker or section
    error = error_marker or f"{section}-error"
    if stored is None:
        return _redirect(
            f"/client/{slug}/application?saved={error}#section-{section}",
        )
    update_draft(ctx["username"], slug, section=section, **{field: stored})
    return _redirect(
        f"/client/{slug}/application?saved={saved}#section-{section}",
    )


def _resolve_download(username: str, slug: str, kind: str) -> Path | None:
    """Resolve the on-disk path for a downloadable application artefact.

    Returns ``None`` if the kind is unknown, the application doesn't
    exist, the field is empty, or the file is missing on disk.
    """
    if kind not in _DOWNLOADABLE_KINDS:
        return None
    application = get_application(username, slug)
    if application is None:
        return None
    filename = getattr(application, _DOWNLOADABLE_KINDS[kind], None)
    if filename is None:
        return None
    return get_upload_path(username, slug, kind, filename)


def _section_fields_from_form(section: str, form: Any) -> dict[str, Any]:
    """Return the ``Application`` field updates implied by a saved section."""
    if section == "about":
        return {
            "full_name": _form_str(form, "full_name"),
            "date_of_birth": _form_str(form, "date_of_birth"),
            "location": _form_str(form, "location"),
            "nationality": _form_str(form, "nationality"),
            "personal_statement": _form_str(form, "personal_statement"),
            "programme": _form_str(form, "programme"),
        }
    if section == "motivation":
        return {"motivation": _form_str(form, "motivation")}
    if section == "experience":
        return {
            "aviation_experience": _form_str(form, "aviation_experience"),
            "work_experience": _form_str(form, "work_experience"),
        }
    if section == "funding":
        return {
            "total_program_cost_gbp": _form_int(form, "total_program_cost_gbp"),
            "funding_requested_gbp": _form_int(form, "funding_requested_gbp"),
            "self_funded_explanation": _form_str(form, "self_funded_explanation"),
        }
    if section == "video":
        # Allow the candidate to set or clear the URL without touching
        # the uploaded-file path.
        return {"video_url": _form_str(form, "video_url").strip() or None}
    return {}


def _dashboard_cta_label(application: Any) -> str:
    """Return the CTA copy shown on the in-flight application banner.

    Editable drafts read "Continue application"; anything that's been
    submitted reads "View application". Encapsulated here so the
    template stays free of conditional copy.
    """
    if application is None:
        return "Open application"
    if getattr(application, "status", "saved") == "saved":
        return "Continue application"
    return "View application"


def _client_layout_context(username: str) -> dict[str, Any]:
    """Return the avatar + role fields shared by every client-side template.

    Returned as ``dict[str, Any]`` so callers can layer additional, mixed-type
    context (applications, testimonials, programme steps) on top without
    fighting the type checker.
    """
    return {
        "role": "client",
        "username": username,
        "avatar_initials": username_initials(username),
        "avatar_color_hex": username_avatar_color_hex(username),
    }


def _resolve_client_program(request: Request, slug: str) -> tuple[Response, None] | tuple[None, dict[str, Any]]:
    """Resolve the auth + programme context for a nested client route.

    Returns a redirect tuple ``(response, None)`` on auth/role failure or
    when the slug is unknown, otherwise ``(None, context)`` where the
    context dict is pre-populated with ``role``, ``username``, avatar
    fields, and the resolved ``program``.
    """
    ctx = get_session_context(request, expected_role="client")
    if ctx is None:
        return _redirect("/"), None
    program = get_program_by_slug(slug)
    if program is None:
        return _redirect("/client"), None
    ctx_data = _client_layout_context(ctx["username"])
    ctx_data["program"] = program
    return None, ctx_data


def _register_client_routes(app: FastAPI) -> None:
    """Attach the client dashboard, programme, testimonials, and apply routes."""

    @app.get("/client", response_class=HTMLResponse)
    async def client_dashboard(request: Request) -> Response:
        """Render the client dashboard.

        Branches on whether the signed-in client has an open application:
        renders a status-stepper banner if so, or the programme widget
        with the three intro cards if not.
        """
        ctx = get_session_context(request, expected_role="client")
        if ctx is None:
            return _redirect("/")
        application = get_application_for(ctx["username"])
        ctx_data = _client_layout_context(ctx["username"])
        ctx_data.update(
            {
                "application": application,
                "programs": get_programs(),
                "status_order": APPLICATION_STATUS_ORDER,
                "status_labels": APPLICATION_STATUS_LABELS,
                "current_step_index": (
                    APPLICATION_STATUS_ORDER.index(application.status)
                    if application is not None
                    else -1
                ),
                "cta_label": _dashboard_cta_label(application),
            },
        )
        return _templates.TemplateResponse(
            request,
            "client_dashboard.html",
            ctx_data,
        )

    @app.get("/client/{slug}/program", response_class=HTMLResponse)
    async def client_program(request: Request, slug: str) -> Response:
        """Render the programme info page for the supplied programme slug."""
        redirect, ctx_data = _resolve_client_program(request, slug)
        if redirect is not None:
            return redirect
        assert ctx_data is not None  # for the type checker  # noqa: S101
        ctx_data["steps"] = _PROGRAM_STEPS
        ctx_data["repayment_terms"] = _REPAYMENT_TERMS
        ctx_data["product_comparison"] = _PRODUCT_COMPARISON
        return _templates.TemplateResponse(
            request,
            "client_program.html",
            ctx_data,
        )

    @app.get("/client/{slug}/testimonials", response_class=HTMLResponse)
    async def client_testimonials(request: Request, slug: str) -> Response:
        """Render the testimonials page scoped to the supplied programme."""
        redirect, ctx_data = _resolve_client_program(request, slug)
        if redirect is not None:
            return redirect
        assert ctx_data is not None  # for the type checker  # noqa: S101
        ctx_data["testimonials"] = get_testimonials()
        return _templates.TemplateResponse(
            request,
            "client_testimonials.html",
            ctx_data,
        )


def _register_client_application_routes(app: FastAPI) -> None:
    """Attach the per-programme application portal routes to ``app``.

    Split out from ``_register_client_routes`` so that function stays
    under the statement-count limit. Covers the portal page itself,
    section-save, three upload endpoints, submission, and the file-
    download endpoint that streams uploaded artefacts back to the user.
    """

    @app.get("/client/{slug}/application", response_class=HTMLResponse)
    async def client_application(
        request: Request,
        slug: str,
        saved: str | None = None,
    ) -> Response:
        """Render the application portal for the supplied programme.

        The page works as both an editable form (while ``application.is_editable``)
        and a read-only summary (after submission). ``saved`` is a
        post-redirect-get marker — when present it identifies the section
        that just saved so the template can show a transient "Saved ✓"
        indicator.
        """
        redirect, ctx_data = _resolve_client_program(request, slug)
        if redirect is not None:
            return redirect
        assert ctx_data is not None  # for the type checker  # noqa: S101
        ctx = get_session_context(request, expected_role="client")
        assert ctx is not None  # for the type checker  # noqa: S101
        application = get_or_create_draft(ctx["username"], slug)
        ctx_data.update(
            {
                "application": application,
                "status_labels": APPLICATION_STATUS_LABELS,
                "sections": APPLICATION_SECTIONS,
                "saved_section": saved,
            },
        )
        return _templates.TemplateResponse(
            request,
            "client_application.html",
            ctx_data,
        )

    @app.post("/client/{slug}/application/save")
    async def client_application_save(request: Request, slug: str) -> Response:
        """Save one section of the application form and redirect back to the page.

        Reads the form via ``request.form()`` so the handler signature
        stays compact; ``_section_fields_from_form`` pulls only the fields
        that belong to the named section, which keeps an unrelated save
        from blanking other parts of the draft.
        """
        ctx = _client_auth(request, slug)
        if isinstance(ctx, RedirectResponse):
            return ctx
        form = await request.form()
        section = _form_str(form, "section")
        if section not in _SAVEABLE_SECTIONS:
            return _redirect(f"/client/{slug}/application")
        # Ensure a draft exists before we update it.
        get_or_create_draft(ctx["username"], slug)
        section_fields = _section_fields_from_form(section, form)
        update_draft(ctx["username"], slug, section=section, **section_fields)
        return _redirect(
            f"/client/{slug}/application?saved={section}#section-{section}",
        )

    @app.post("/client/{slug}/application/upload/cv")
    async def client_application_upload_cv(
        request: Request,
        slug: str,
        cv: UploadFile,
    ) -> Response:
        """Save an uploaded CV and mark the CV section complete."""
        return await _handle_upload(
            request,
            slug,
            upload=cv,
            section="cv",
            store=store_cv_upload,
            field="cv_filename",
        )

    @app.post("/client/{slug}/application/upload/video")
    async def client_application_upload_video(
        request: Request,
        slug: str,
        video: UploadFile,
    ) -> Response:
        """Save an uploaded intro video and mark the video section complete."""
        return await _handle_upload(
            request,
            slug,
            upload=video,
            section="video",
            store=store_video_upload,
            field="video_filename",
        )

    @app.post("/client/{slug}/application/upload/proof-of-funds")
    async def client_application_upload_proof(
        request: Request,
        slug: str,
        proof_of_funds: UploadFile,
    ) -> Response:
        """Save an uploaded proof-of-funds document under the Funding section."""
        return await _handle_upload(
            request,
            slug,
            upload=proof_of_funds,
            section="funding",
            store=store_proof_of_funds_upload,
            field="proof_of_funds_filename",
            saved_marker="proof",
            error_marker="proof-error",
        )

    @app.post("/client/{slug}/application/submit")
    async def client_application_submit(request: Request, slug: str) -> Response:
        """Lock the draft and return to the dashboard."""
        ctx = _client_auth(request, slug)
        if isinstance(ctx, RedirectResponse):
            return ctx
        submit_application(ctx["username"], slug)
        return _redirect("/client")

    @app.get("/client/{slug}/application/files/{kind}")
    async def client_application_download(
        request: Request,
        slug: str,
        kind: str,
    ) -> Response:
        """Stream a previously uploaded artefact back to the candidate."""
        ctx = _client_auth(request, slug)
        if isinstance(ctx, RedirectResponse):
            return ctx
        path = _resolve_download(ctx["username"], slug, kind)
        if path is None:
            return _redirect(f"/client/{slug}/application")
        return FileResponse(path, filename=path.name)


def create_app() -> FastAPI:
    """Build and configure the FastAPI application.

    Reads the session secret from config so cookies survive restarts. Falls
    back to a freshly generated token only when no config is present (e.g.
    in isolated unit tests).

    Returns:
        A fully configured ``FastAPI`` instance with session middleware and
        all routes registered.

    """
    _config_path = Path(__file__).parents[2] / "config" / "education_funding.yaml"
    _config = EducationFundingConfig(str(_config_path))
    app = FastAPI(title="Education Financing", docs_url=None, redoc_url=None)
    app.add_middleware(
        SessionMiddleware,
        secret_key=_config.session_secret,
        same_site="lax",
        https_only=False,
    )
    _register_auth_routes(app)
    _register_investor_routes(app)
    _register_client_routes(app)
    _register_client_application_routes(app)
    return app


app = create_app()
"""Module-level ASGI app, used by the uvicorn import string in ``run.py``."""
