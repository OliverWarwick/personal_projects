"""ClubSpark browser-automation client.

Uses Playwright (headless Chromium) to load the ClubSpark booking page and
extract court-availability data from the dynamically-rendered DOM, and to
perform authenticated court bookings.

ClubSpark's booking interface is a single-page application: the page shell is
served as static HTML, but the booking grid is injected by JavaScript after
the page reads the ``#?date=...&role=...`` URL fragment.  Simple HTTP requests
cannot retrieve the grid, so we drive a real browser instead.

Authentication uses WS-Federation via ``auth.clubspark.uk``.  Playwright
follows the redirect chain automatically; session state can optionally be
persisted to disk so that re-login is only required when the session expires.

DOM structure (as of 2026-03)
------------------------------
The booking grid is a ``.booking-sheet`` container.  Inside it, each court is
represented by a ``div.resource`` element carrying a ``data-resource-name``
attribute.  Within each resource, individual time slots are
``div.resource-session`` elements whose ``data-start-time`` and
``data-end-time`` attributes hold the slot boundaries as **minutes from
midnight** (e.g. 540 = 09:00).  Availability is signalled by
``data-availability="true"`` and the price by ``data-session-cost``.

Payment (as of 2026-03)
-----------------------
After clicking "Continue booking", ClubSpark navigates to
``/Booking/Book?...`` which embeds a Stripe Elements form.  The three card
fields (number, expiry, CVC) are rendered in individual cross-origin Stripe
iframes.  Playwright interacts with these via ``frame_locator``.  Card details
are supplied as an optional :class:`CardDetails` argument to
:meth:`ClubSparkClient.book_slot` and are loaded from the OS keyring by the
service layer.

If ClubSpark changes its front-end, update the ``_SELECTOR_*`` and
``_ATTR_*`` constants near the top of this module.
"""

from __future__ import annotations

import datetime
import logging
import pathlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from playwright.async_api import async_playwright

if TYPE_CHECKING:
    from playwright.async_api import Browser, BrowserContext, ElementHandle, FrameLocator, Page

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Selector constants - update here if ClubSpark changes its DOM
# ---------------------------------------------------------------------------

# The outer wrapper that appears once the booking grid has been rendered.
_SELECTOR_GRID_ROOT: Final = ".booking-sheet"

# Each court column is a div.resource element carrying a data-resource-name.
_SELECTOR_RESOURCE: Final = "div.resource[data-resource-name]"

# Each 30-minute bookable interval within a court column.  Intervals live
# inside resource-session elements, but the interval is the atomic bookable
# unit: an available session can contain many consecutive intervals.
_SELECTOR_INTERVAL: Final = "div.resource-interval"

# Each bookable session within a court column.  Sessions carry the start/end
# time boundaries and availability attributes parsed by _parse_session.
_SELECTOR_SESSION: Final = "div.resource-session"

# The booking anchor present inside an available interval.
_SELECTOR_BOOK_LINK: Final = "a.book-interval"

# Price span inside an available booking anchor.
_SELECTOR_COST: Final = "span.cost"

# ---------------------------------------------------------------------------
# Login / booking selectors (auth.clubspark.uk and booking confirmation page)
# ---------------------------------------------------------------------------

# Email and password inputs on the LTA login form.
# On auth.clubspark.uk: button that redirects to the LTA Advantage identity provider.
_SELECTOR_LTA_LOGIN_BUTTON: Final = "button[name='idp'][value='LTA2']"

# On mylta.my.site.com (Salesforce): the LTA Advantage username / password form.
_SELECTOR_LOGIN_EMAIL: Final = "input[placeholder='Username']"
_SELECTOR_LOGIN_PASSWORD: Final = "input[placeholder='Password']"  # noqa: S105
_SELECTOR_LOGIN_SUBMIT: Final = "button.slds-button_brand:has-text('Log in')"

# After login, ClubSpark redirects back; this nav element confirms a logged-in state.
# ClubSpark updated its header in 2026-03: the old .user-name / .logout-link selectors
# were replaced with a button-based account avatar.
_SELECTOR_LOGGED_IN: Final = (
    ".user-name, "
    ".logout-link, "
    "a[href*='SignOut'], "
    "a[href*='LogOff'], "
    ".ui-account-avatar-btn, "
    ".ui-header-v2-mobile-account-btn-logged-in"
)

# Cookie consent overlay (Osano) — dismiss before interacting with the booking form.
_SELECTOR_COOKIE_ACCEPT: Final = ".osano-cm-accept-all, .osano-cm-dialog__close"

# Booking confirmation modal — the "Continue booking" submit inside the Book form.
# ClubSpark changed from <input value='Continue booking'> to <button id='submit-booking'>
# in 2026-03; keep both selectors for resilience.
_SELECTOR_CONFIRM_BUTTON: Final = "button#submit-booking, input[value='Continue booking']"

# Final confirm button shown after the "Continue booking" step (next page).
_SELECTOR_FINAL_CONFIRM: Final = (
    "input[value='Confirm booking'], "
    "input[value*='Confirm'], "
    "button[type='submit']"
)

# Success indicator after confirming (URL fragment or element on the page).
# ClubSpark's confirmation page (as of 2026-03) uses a .success div containing
# "Your booking has been confirmed", and the booking ID appears in the URL path
# at /Booking/BookingConfirmation/{uuid}.
_SELECTOR_BOOKING_SUCCESS: Final = (
    ".success, "
    ".booking-confirmed, "
    ".confirmation-number, "
    ".booking-reference, "
    "h1.confirmed, "
    "[data-booking-reference], "
    ".booking-complete"
)

# ---------------------------------------------------------------------------
# Stripe Elements payment selectors (payment page at /Booking/Book?...)
# ---------------------------------------------------------------------------

# "Confirm and pay" button that reveals the Stripe Elements inline form when clicked.
_SELECTOR_STRIPE_REVEAL_BUTTON: Final = "#paynow"

# Submit button on the Stripe Elements payment form; disabled until card fields
# are filled.
_SELECTOR_STRIPE_PAY_BUTTON: Final = "#cs-stripe-elements-submit-button"

# Stripe Element iframe wrappers — each contains a single cross-origin iframe.
_SELECTOR_STRIPE_CARD_NUMBER_WRAP: Final = "#cs-stripe-elements-card-number"
_SELECTOR_STRIPE_CARD_EXPIRY_WRAP: Final = "#cs-stripe-elements-card-expiry"
_SELECTOR_STRIPE_CARD_CVC_WRAP: Final = "#cs-stripe-elements-card-cvc"

# Input name attributes used inside the Stripe cross-origin iframes.
_STRIPE_CARDNUMBER_INPUT: Final = '[name="cardnumber"]'
_STRIPE_EXPIRY_INPUT: Final = '[name="exp-date"]'
_STRIPE_CVC_INPUT: Final = '[name="cvc"]'

# Success page URL pattern after Stripe payment completes.
_STRIPE_SUCCESS_URL_PATTERN: Final = "**/Booking/BookingConfirmation/**"

# Timeout (ms) to wait for the Stripe payment iframes to be ready.
_STRIPE_READY_TIMEOUT_MS: Final = 20_000

# Timeout (ms) to wait for the payment to complete and redirect.
_STRIPE_PAYMENT_TIMEOUT_MS: Final = 30_000

# ---------------------------------------------------------------------------
# Attribute constants on resource / session elements
# ---------------------------------------------------------------------------

# Court name on the resource div.
_ATTR_RESOURCE_NAME: Final = "data-resource-name"

# Slot boundaries on the session div (minutes from midnight).
_ATTR_START_TIME: Final = "data-start-time"
_ATTR_END_TIME: Final = "data-end-time"

# Availability flag on the session div.
_ATTR_AVAILABILITY: Final = "data-availability"

# String value of data-availability when the slot is bookable.
_AVAILABLE_VALUE: Final = "true"

# Optional price string on the session div.
_ATTR_SESSION_COST: Final = "data-session-cost"

# CSS class present on the booking anchor when the interval is still bookable.
_CSS_NOT_BOOKED: Final = "not-booked"

# Timeout (ms) to wait for the booking grid to appear after navigation.
_GRID_LOAD_TIMEOUT_MS: Final = 15_000

# Timeout (ms) for the login redirect round-trip and confirmation page.
_LOGIN_TIMEOUT_MS: Final = 20_000

# Timeout (ms) to wait for the booking confirmation page to appear.
_BOOKING_TIMEOUT_MS: Final = 15_000

# Default path to persist Playwright browser storage state (cookies/localStorage).
_DEFAULT_SESSION_PATH: Final = pathlib.Path.home() / ".config" / "tennis-court-booker" / "clubspark_session.json"

# Base URL of the ClubSpark LTA portal.
_BASE_URL: Final = "https://clubspark.lta.org.uk"

# Identity provider host that serves the WS-Federation login form.
_AUTH_HOST: Final = "auth.clubspark.uk"


# ---------------------------------------------------------------------------
# Raw data transfer objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RawSlot:
    """Raw slot data parsed directly from the DOM before domain modelling.

    Args:
        court_name: The human-readable name of the court (e.g. ``"Crt 1"``).
        start_time: The start time of the slot.
        end_time: The end time of the slot.
        is_available: ``True`` if the slot can be booked.
        price: Optional price string scraped from the session element
            (e.g. ``"3.60"``).

    """

    court_name: str
    start_time: datetime.time
    end_time: datetime.time
    is_available: bool
    price: str | None = None


# ---------------------------------------------------------------------------
# Public client
# ---------------------------------------------------------------------------


class ClubSparkClient:
    """Async client for scraping availability and booking courts on ClubSpark.

    Launch a headless Chromium browser via Playwright, navigate to the
    ClubSpark booking-by-date page for the requested venue, wait for the
    booking grid to render, and then parse the DOM to return structured slot
    data.

    For booking, the client authenticates using LTA account credentials
    (stored in the OS keyring via
    :class:`~src.clients.clubspark.credentials.ClubSparkCredentialHelper`
    or supplied directly).  Session state (cookies) is optionally persisted to
    ``session_path`` so re-login is only required when the session expires.

    Usage::

        client = ClubSparkClient()
        slots = await client.get_available_slots(
            "BurgessParkSouthwark",
            datetime.date(2026, 3, 10),
        )

        result = await client.book_slot(
            "BurgessParkSouthwark",
            datetime.date(2026, 3, 14),
            "Crt 1",
            datetime.time(19, 0),
            email="you@example.com",
            password="secret",
        )

    Availability scraping is stateless; a new browser context is created and
    closed per call.  Booking reuses the session saved to *session_path* when
    available, falling back to a fresh login when the session is stale.
    """

    def __init__(
        self,
        *,
        headless: bool = True,
        timeout_ms: int = _GRID_LOAD_TIMEOUT_MS,
        session_path: pathlib.Path | None = None,
    ) -> None:
        """Initialise the client.

        Args:
            headless: Run Chromium in headless mode.  Set to ``False`` when
                debugging selector issues so you can observe the browser.
            timeout_ms: Maximum time in milliseconds to wait for the booking
                grid to appear after navigation.
            session_path: Path to a JSON file used to persist browser storage
                state (cookies / localStorage) between booking calls.  When
                ``None`` the default path
                ``~/.config/tennis-court-booker/clubspark_session.json`` is
                used.  Pass ``pathlib.Path(os.devnull)`` to disable
                persistence.

        """
        self._headless = headless
        self._timeout_ms = timeout_ms
        self._session_path: pathlib.Path = session_path or _DEFAULT_SESSION_PATH

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_available_slots(
        self,
        venue: str,
        date: datetime.date,
    ) -> list[RawSlot]:
        """Fetch all court slots (available and unavailable) for a venue on a date.

        Navigate to the ClubSpark booking page for *venue* on *date* as a
        guest, wait for the booking grid to render, and return every slot found
        in the grid, regardless of availability status.

        Args:
            venue: The venue slug as it appears in the ClubSpark URL, e.g.
                ``"BurgessParkSouthwark"``.
            date: The date for which to fetch availability.

        Returns:
            A list of :class:`RawSlot` instances, one per (court, time-slot)
            combination found in the booking grid.

        Raises:
            TimeoutError: If the booking grid does not render within
                ``timeout_ms`` milliseconds.
            ValueError: If no courts can be parsed from the page.

        """
        async with async_playwright() as playwright:
            browser: Browser = await playwright.chromium.launch(headless=self._headless)
            try:
                page: Page = await browser.new_page()
                return await self._scrape_slots(page, venue, date)
            finally:
                await browser.close()

    async def is_slot_available(
        self,
        venue: str,
        date: datetime.date,
        court_name: str,
        start_time: datetime.time,
    ) -> bool:
        """Check whether a specific court slot is still bookable.

        This is a convenience wrapper around :meth:`get_available_slots` that
        filters to the requested (court, start_time) combination.

        Args:
            venue: ClubSpark venue slug (e.g. ``"BurgessParkSouthwark"``).
            date: The date to check.
            court_name: Exact court name as shown in the booking grid.
            start_time: The start time of the slot to check.

        Returns:
            ``True`` if the slot exists in the grid and is currently available,
            ``False`` otherwise (including when the slot does not exist).

        """
        slots = await self.get_available_slots(venue, date)
        for slot in slots:
            if slot.court_name == court_name and slot.start_time == start_time:
                return slot.is_available
        return False

    async def book_slot(
        self,
        venue: str,
        date: datetime.date,
        court_name: str,
        start_time: datetime.time,
        *,
        email: str,
        password: str,
        card_number: str | None = None,
        expiry_mmyy: str | None = None,
        security_code: str | None = None,
    ) -> str | None:
        """Log in and book a specific court slot at a ClubSpark venue.

        The full booking flow:

        1. Launch a Chromium browser, restoring any saved session from
           ``session_path``.
        2. Navigate to the venue booking page.  If the session is stale
           (no logged-in indicator found), perform a fresh LTA login.
        3. Locate the ``a.book-interval`` anchor for *court_name* at
           *start_time* and click it.
        4. Click "Continue booking" — ClubSpark navigates to the payment page
           at ``/Booking/Book?...``.
        5. If card credentials are provided, fill the Stripe Elements iframes
           and submit payment.  If card credentials are absent and the booking
           requires payment, a :class:`RuntimeError` is raised.
        6. Persist the updated session state back to ``session_path``.

        Args:
            venue: ClubSpark venue slug (e.g. ``"BurgessParkSouthwark"``).
            date: The date of the slot to book.
            court_name: Exact court name as shown in the booking grid
                (e.g. ``"Crt 1"``).
            start_time: Start time of the slot to book.
            email: LTA account email address.
            password: LTA account password.
            card_number: 16-digit card number (no spaces).  Required when the
                slot charges a booking fee.
            expiry_mmyy: Card expiry in ``MMYY`` format, e.g. ``"1225"``.
            security_code: 3- or 4-digit CVV / security code.

        Returns:
            A booking-reference string on success, or ``None`` if no
            reference element was found on the confirmation page (the booking
            may still have succeeded — check your LTA account).

        Raises:
            RuntimeError: If login fails, the target slot cannot be found,
                or a payment form is encountered but no card credentials were
                supplied.
            TimeoutError: If any page element does not appear within the
                configured timeout.

        """
        async with async_playwright() as playwright:
            browser: Browser = await playwright.chromium.launch(headless=self._headless)
            try:
                context: BrowserContext = await self._make_context(browser)
                page: Page = await context.new_page()

                await self._navigate_booking_page(page, venue, date)

                if not await self._is_logged_in(page):
                    logger.debug("Session stale or absent — performing fresh login for %s", email)
                    await self._login(page, venue, email, password)
                    # Re-navigate to the target date: the login return URL lands on
                    # today's booking page without the date fragment.
                    await self._navigate_booking_page(page, venue, date)

                reference = await self._click_and_confirm(
                    page, venue, court_name, start_time,
                    card_number=card_number,
                    expiry_mmyy=expiry_mmyy,
                    security_code=security_code,
                )
                await self._save_session(context)
                return reference
            finally:
                await browser.close()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _make_context(self, browser: Browser) -> BrowserContext:
        """Return a browser context, restoring saved session state when available.

        Args:
            browser: An open :class:`Browser` instance.

        Returns:
            A :class:`BrowserContext` pre-loaded with saved cookies and
            storage state if ``session_path`` exists, otherwise a fresh
            context.

        """
        if self._session_path.exists():
            logger.debug("Restoring session from %s", self._session_path)
            return await browser.new_context(storage_state=str(self._session_path))
        return await browser.new_context()

    async def _save_session(self, context: BrowserContext) -> None:
        """Persist browser storage state (cookies) to ``session_path``.

        Creates the parent directory if it does not already exist.

        Args:
            context: The :class:`BrowserContext` whose state should be saved.

        """
        self._session_path.parent.mkdir(parents=True, exist_ok=True)
        await context.storage_state(path=str(self._session_path))
        logger.debug("Session saved to %s", self._session_path)

    async def _navigate_booking_page(self, page: Page, venue: str, date: datetime.date) -> None:
        """Navigate to the booking page for *venue* on *date* and wait for the grid.

        ClubSpark's booking page is a fragment-routed SPA.  A plain ``goto``
        to the same base URL with a different hash does not trigger a new HTTP
        request, so the SPA may not re-render.  To guarantee a fresh load we
        navigate to an intermediate URL first (the venue home page), then
        navigate to the full booking URL with the date fragment.

        Args:
            page: An open Playwright :class:`Page`.
            venue: ClubSpark venue slug.
            date: Target booking date.

        """
        # Navigate away from any existing booking page to force a clean SPA load.
        intermediate = f"{_BASE_URL}/{venue}"
        await page.goto(intermediate, wait_until="domcontentloaded")

        url = f"{_BASE_URL}/{venue}/Booking/BookByDate#?date={date.isoformat()}&role=member"
        logger.debug("Navigating to booking page: %s", url)
        await page.goto(url, wait_until="domcontentloaded")

    async def _is_logged_in(self, page: Page) -> bool:
        """Return ``True`` if the current page shows a logged-in navigation state.

        Checks for the presence of any element matching
        ``_SELECTOR_LOGGED_IN`` (e.g. a logout link or username display).

        Args:
            page: The current Playwright :class:`Page`.

        Returns:
            ``True`` when a logged-in indicator is found, ``False`` otherwise.

        """
        try:
            await page.wait_for_selector(_SELECTOR_LOGGED_IN, timeout=3_000)
        except Exception:
            return False
        else:
            return True

    async def _login(
        self,
        page: Page,
        venue: str,
        email: str,
        password: str,
    ) -> None:
        """Perform the two-hop LTA login flow.

        The full redirect chain is:

        1. Navigate to the venue-scoped ClubSpark sign-in URL.
        2. ``auth.clubspark.uk`` renders two options; click the "Login" button
           (``idp=LTA2``) to federate to LTA Advantage.
        3. ``mylta.my.site.com`` (Salesforce) renders the LTA email/password
           form; fill and submit it.
        4. LTA redirects back through ``auth.clubspark.uk`` to the original
           ClubSpark booking page.

        Args:
            page: The current Playwright :class:`Page`.
            venue: ClubSpark venue slug (used to construct the sign-in and
                return URLs).
            email: LTA account email address (username).
            password: LTA account password.

        Raises:
            RuntimeError: If the page does not redirect back to ClubSpark
                within ``_LOGIN_TIMEOUT_MS`` milliseconds, indicating that
                the credentials were rejected or the redirect chain failed.

        """
        return_url = f"/{venue}/Booking/BookByDate"
        sign_in_url = f"{_BASE_URL}/{venue}/Account/SignIn?returnUrl={return_url}"
        logger.debug("Navigating to sign-in URL: %s", sign_in_url)
        await page.goto(sign_in_url, wait_until="domcontentloaded")

        # Step 1: on auth.clubspark.uk — click the LTA Advantage login button.
        await page.wait_for_url(f"**/{_AUTH_HOST}/**", timeout=_LOGIN_TIMEOUT_MS)
        await page.wait_for_selector(_SELECTOR_LTA_LOGIN_BUTTON, timeout=_LOGIN_TIMEOUT_MS)
        await page.click(_SELECTOR_LTA_LOGIN_BUTTON)

        # Step 2: on mylta.my.site.com (Salesforce) — fill the LTA login form.
        await page.wait_for_load_state("networkidle")
        await page.wait_for_selector(_SELECTOR_LOGIN_EMAIL, timeout=_LOGIN_TIMEOUT_MS)
        await page.fill(_SELECTOR_LOGIN_EMAIL, email)
        await page.fill(_SELECTOR_LOGIN_PASSWORD, password)
        await page.click(_SELECTOR_LOGIN_SUBMIT)

        # Step 3: wait for the redirect back to the ClubSpark booking page.
        try:
            await page.wait_for_url(
                f"**/{venue}/Booking/BookByDate**",
                timeout=_LOGIN_TIMEOUT_MS,
            )
        except Exception as exc:
            msg = (
                f"Login did not redirect back to the booking page for venue '{venue}'. "
                "Check your LTA credentials."
            )
            raise RuntimeError(msg) from exc

        logger.debug("Login successful for %s", email)

    async def _click_and_confirm(
        self,
        page: Page,
        venue: str,
        court_name: str,
        start_time: datetime.time,
        *,
        card_number: str | None = None,
        expiry_mmyy: str | None = None,
        security_code: str | None = None,
    ) -> str | None:
        """Click the booking link for the target slot and confirm the booking.

        Waits for the booking grid, locates the ``a.book-interval`` anchor
        inside the matching court / session element, clicks it, then handles
        the confirmation page including Stripe payment when required.

        Args:
            page: The current authenticated Playwright :class:`Page`.
            venue: ClubSpark venue slug (used in error messages).
            court_name: Exact court name to match against
                ``data-resource-name``.
            start_time: Start time of the target slot (matched against
                ``data-start-time`` minutes-from-midnight).
            card_number: Optional 16-digit card number for Stripe payment.
            expiry_mmyy: Optional card expiry in ``MMYY`` format.
            security_code: Optional CVV / security code.

        Returns:
            A booking reference string if one is found on the confirmation
            page, otherwise ``None``.

        Raises:
            TimeoutError: If the booking grid does not appear.
            RuntimeError: If the target slot or its booking link cannot be
                found on the page.

        """
        try:
            await page.wait_for_selector(_SELECTOR_GRID_ROOT, timeout=self._timeout_ms)
        except Exception as exc:
            msg = (
                f"Booking grid did not appear for venue '{venue}'. "
                "Session may have expired."
            )
            raise TimeoutError(msg) from exc

        start_minutes = start_time.hour * 60 + start_time.minute
        book_link = await self._find_book_link(page, court_name, start_minutes)
        if book_link is None:
            msg = (
                f"No available booking link found for court '{court_name}' "
                f"at {start_time.strftime('%H:%M')} on the booking grid. "
                "The slot may already be taken."
            )
            raise RuntimeError(msg)

        # Dismiss the cookie banner before clicking the booking link.  Dismissing
        # it after the booking modal is open closes the modal on current ClubSpark.
        await self._dismiss_cookie_banner(page)

        logger.debug(
            "Clicking booking link for court '%s' at %s",
            court_name, start_time.strftime("%H:%M"),
        )
        await book_link.click()

        # Extend the booking to 1 hour by updating the hidden EndTime field before
        # the form is submitted.  The field holds minutes-from-midnight; adding 60
        # to StartTime gives a 1-hour slot.  This is best-effort: if the field is
        # absent (e.g. ClubSpark changes the form), we proceed with the default
        # duration set by the page.
        try:
            await page.wait_for_selector("#end-time", timeout=5_000)
            await page.evaluate(
                f'document.getElementById("end-time").value = "{start_minutes + 60}"'
            )
            logger.debug("Set end-time to %d minutes (1-hour slot)", start_minutes + 60)
        except Exception:
            logger.debug("Could not update #end-time field; proceeding with default duration")

        return await self._handle_confirmation_page(
            page,
            card_number=card_number,
            expiry_mmyy=expiry_mmyy,
            security_code=security_code,
        )

    async def _find_book_link(
        self,
        page: Page,
        court_name: str,
        start_minutes: int,
    ) -> ElementHandle | None:
        """Locate the ``a.book-interval`` element for a specific court and time.

        Iterates over every ``.resource`` element to find the one whose
        ``data-resource-name`` matches *court_name*, then searches within it
        for a ``.resource-session`` whose ``data-start-time`` matches
        *start_minutes*, and returns the ``a.book-interval`` anchor inside it.

        Args:
            page: The current Playwright :class:`Page`.
            court_name: Exact court name to match.
            start_minutes: Slot start time expressed as minutes from midnight.

        Returns:
            The ``a.book-interval`` :class:`ElementHandle`, or ``None`` if no
            matching available slot is found.

        """
        resources = await page.query_selector_all(_SELECTOR_RESOURCE)
        for resource in resources:
            name = await resource.get_attribute(_ATTR_RESOURCE_NAME) or ""
            if name != court_name:
                continue

            sessions = await resource.query_selector_all(_SELECTOR_SESSION)
            for session in sessions:
                start_attr = await session.get_attribute(_ATTR_START_TIME)
                if start_attr is None or int(start_attr) != start_minutes:
                    continue

                link = await session.query_selector(_SELECTOR_BOOK_LINK)
                if link is not None:
                    return link
        return None

    async def _dismiss_cookie_banner(self, page: Page) -> None:
        """Dismiss the Osano cookie consent banner if it is visible.

        Tries to click the "Accept All" or close button.  Silently does
        nothing if the banner is not present.

        Args:
            page: The current Playwright :class:`Page`.

        """
        try:
            banner = await page.wait_for_selector(_SELECTOR_COOKIE_ACCEPT, timeout=3_000)
            if banner and await banner.is_visible():
                await banner.click()
                logger.debug("Cookie consent banner dismissed")
        except Exception:
            logger.debug("No cookie consent banner detected")

    async def _handle_confirmation_page(
        self,
        page: Page,
        *,
        card_number: str | None = None,
        expiry_mmyy: str | None = None,
        security_code: str | None = None,
    ) -> str | None:
        """Click through the ClubSpark booking confirmation flow and return the reference.

        After clicking a booking link, ClubSpark renders a booking summary
        modal.  Clicking "Continue booking" navigates to the payment page at
        ``/Booking/Book?...``.  If Stripe Elements are present on that page,
        the supplied card credentials are used to fill the cross-origin iframes
        and submit payment.

        Flow:

        1. ``"Continue booking"`` — the submit button inside the booking
           summary modal.  Clicking it triggers a full-page navigation to
           ``/Booking/Book?...``.
        2. If a Stripe Elements payment form is present, fill card details
           and click "Pay".
        3. Wait for the booking-confirmed redirect and extract the reference.

        Args:
            page: The current Playwright :class:`Page`.
            card_number: Optional 16-digit card number for Stripe payment.
            expiry_mmyy: Optional card expiry in ``MMYY`` format.
            security_code: Optional CVV / security code.

        Returns:
            A booking-reference string extracted from the confirmation page,
            or ``None`` when the page does not expose a parseable reference
            element (the booking may still have succeeded).

        Raises:
            TimeoutError: If the "Continue booking" button or Stripe iframes
                do not appear within the configured timeouts.
            RuntimeError: If a payment form is present but no card credentials
                were supplied.

        """
        try:
            await page.wait_for_selector(_SELECTOR_CONFIRM_BUTTON, timeout=_BOOKING_TIMEOUT_MS)
        except Exception as exc:
            msg = (
                "Booking confirmation button did not appear. "
                "ClubSpark may have changed its confirmation page layout."
            )
            raise TimeoutError(msg) from exc

        logger.debug("Clicking 'Continue booking'")
        async with page.expect_navigation(wait_until="domcontentloaded", timeout=_BOOKING_TIMEOUT_MS):
            await page.click(_SELECTOR_CONFIRM_BUTTON)
        await page.wait_for_load_state("networkidle")

        # If the payment page has loaded ("Confirm and pay" button present), pay now.
        reveal_btn = await page.query_selector(_SELECTOR_STRIPE_REVEAL_BUTTON)
        if reveal_btn is not None:
            logger.debug("Stripe payment page detected — proceeding with card payment")
            return await self._pay_stripe_elements(
                page,
                card_number=card_number,
                expiry_mmyy=expiry_mmyy,
                security_code=security_code,
            )

        # Legacy: handle an optional second confirmation step (no payment).
        final_btn = await page.query_selector(_SELECTOR_FINAL_CONFIRM)
        if final_btn and await final_btn.is_visible():
            logger.debug("Clicking final confirm button")
            async with page.expect_navigation(wait_until="domcontentloaded", timeout=_BOOKING_TIMEOUT_MS):
                await final_btn.click()
            await page.wait_for_load_state("networkidle")

        return await self._extract_booking_reference(page)

    async def _pay_stripe_elements(
        self,
        page: Page,
        *,
        card_number: str | None,
        expiry_mmyy: str | None,
        security_code: str | None,
    ) -> str | None:
        """Fill the Stripe Elements payment form and submit it.

        ClubSpark embeds three Stripe Element iframes on the payment page —
        one each for card number, expiry, and CVC.  This method waits for
        each iframe to become interactive, types the card details into the
        cross-origin inputs via Playwright :class:`FrameLocator`, then clicks
        the "Pay" button and waits for the booking-confirmed redirect.

        The ``expiry_mmyy`` value stored in the keyring uses the compact
        ``MMYY`` format (e.g. ``"1225"``).  Stripe's expiry field expects
        ``MM / YY``; this method inserts a space-slash-space separator
        automatically when the raw value has no separator.

        Args:
            page: The Playwright :class:`Page` already showing the payment
                form at ``/Booking/Book?...``.
            card_number: 16-digit card number (no spaces).  When ``None`` a
                :class:`RuntimeError` is raised.
            expiry_mmyy: Card expiry in ``MMYY`` format, e.g. ``"1225"``.
                When ``None`` a :class:`RuntimeError` is raised.
            security_code: 3- or 4-digit CVV.  When ``None`` a
                :class:`RuntimeError` is raised.

        Returns:
            A booking-reference string if found on the confirmation page,
            otherwise ``None``.

        Raises:
            RuntimeError: If any of the card credential arguments is ``None``.
            TimeoutError: If the Stripe iframes or the post-payment
                confirmation URL do not appear within the configured timeouts.

        """
        if card_number is None or expiry_mmyy is None or security_code is None:
            msg = (
                "A payment form was encountered but no card details were supplied. "
                "Run: tennis-court-booker card-credentials set"
            )
            raise RuntimeError(msg)

        # Stripe's expiry field expects "MM / YY"; convert from "MMYY" if needed.
        if len(expiry_mmyy) == 4 and "/" not in expiry_mmyy:  # noqa: PLR2004
            expiry_display = f"{expiry_mmyy[:2]} / {expiry_mmyy[2:]}"
        else:
            expiry_display = expiry_mmyy

        # Click the "Confirm and pay" button to reveal the Stripe Elements inline form.
        logger.debug("Clicking 'Confirm and pay' to reveal Stripe Elements form")
        await page.click(_SELECTOR_STRIPE_REVEAL_BUTTON)

        # Each Stripe Element wrapper contains two iframes: the card field and a
        # Stripe Link button.  Use .first to target the card field iframe only.
        def _stripe_frame(wrapper_selector: str) -> FrameLocator:
            """Return a FrameLocator for the first Stripe Element iframe inside *wrapper_selector*."""
            return page.frame_locator(f"{wrapper_selector} iframe").first

        card_frame = _stripe_frame(_SELECTOR_STRIPE_CARD_NUMBER_WRAP)
        expiry_frame = _stripe_frame(_SELECTOR_STRIPE_CARD_EXPIRY_WRAP)
        cvc_frame = _stripe_frame(_SELECTOR_STRIPE_CARD_CVC_WRAP)

        logger.debug("Waiting for Stripe Elements iframes to be ready")
        await card_frame.locator(_STRIPE_CARDNUMBER_INPUT).wait_for(timeout=_STRIPE_READY_TIMEOUT_MS)

        logger.debug("Filling Stripe Elements card fields")
        await card_frame.locator(_STRIPE_CARDNUMBER_INPUT).fill(card_number)
        await expiry_frame.locator(_STRIPE_EXPIRY_INPUT).fill(expiry_display)
        await cvc_frame.locator(_STRIPE_CVC_INPUT).fill(security_code)

        # Wait for the Pay button to become enabled (Stripe validates fields on input).
        logger.debug("Waiting for Pay button to be enabled")
        await page.wait_for_function(
            "!document.getElementById('cs-stripe-elements-submit-button').disabled",
            timeout=_STRIPE_READY_TIMEOUT_MS,
        )

        logger.debug("Submitting Stripe payment")
        await page.click(_SELECTOR_STRIPE_PAY_BUTTON)
        # Wait for the final booking-confirmation URL; payment may redirect through
        # several hops before landing on /Booking/BookingConfirmation/{uuid}.
        try:
            await page.wait_for_url(
                _STRIPE_SUCCESS_URL_PATTERN,
                timeout=_STRIPE_PAYMENT_TIMEOUT_MS,
            )
        except Exception as exc:
            msg = (
                f"Payment was submitted but the confirmation page did not load within "
                f"{_STRIPE_PAYMENT_TIMEOUT_MS // 1000}s.  "
                f"Current URL: {page.url}"
            )
            raise TimeoutError(msg) from exc
        await page.wait_for_load_state("networkidle")

        return await self._extract_booking_reference(page)

    async def _extract_booking_reference(self, page: Page) -> str | None:
        """Attempt to extract a booking reference from the current page.

        Checks the current URL for a ``/BookingConfirmation/{uuid}`` path
        segment first, as ClubSpark (2026-03+) embeds the booking ID there.
        Falls back to looking for elements matching
        :data:`_SELECTOR_BOOKING_SUCCESS` and returns their text content.
        Logs a warning and returns ``None`` when neither is found.

        Args:
            page: The Playwright :class:`Page` to inspect.

        Returns:
            The booking-reference string (UUID or element text), or ``None``
            if no reference can be found.

        """
        import re as _re

        url = page.url
        url_match = _re.search(r"/BookingConfirmation/([0-9a-f-]{36})", url, _re.IGNORECASE)
        if url_match:
            ref = url_match.group(1)
            logger.debug("Booking reference extracted from URL: %s", ref)
            return ref

        try:
            await page.wait_for_selector(_SELECTOR_BOOKING_SUCCESS, timeout=_BOOKING_TIMEOUT_MS)
        except Exception:
            logger.warning(
                "No booking-success element found after confirming. "
                "The booking may still have succeeded — check your LTA account."
            )
            return None

        ref_el = await page.query_selector(_SELECTOR_BOOKING_SUCCESS)
        if ref_el is not None:
            reference = await ref_el.text_content()
            return (reference or "").strip() or None
        return None

    async def _scrape_slots(
        self,
        page: Page,
        venue: str,
        date: datetime.date,
    ) -> list[RawSlot]:
        """Navigate to the booking page and parse the rendered grid.

        Args:
            page: An open Playwright :class:`Page` instance.
            venue: ClubSpark venue slug.
            date: Date for which to retrieve the booking grid.

        Returns:
            Parsed list of :class:`RawSlot` objects.

        Raises:
            TimeoutError: If the grid selector does not appear in time.
            ValueError: If no courts can be parsed from the grid.

        """
        url = f"{_BASE_URL}/{venue}/Booking/BookByDate#?date={date.isoformat()}&role=guest"
        logger.debug("Navigating to %s", url)
        await page.goto(url, wait_until="domcontentloaded")

        try:
            await page.wait_for_selector(_SELECTOR_GRID_ROOT, timeout=self._timeout_ms)
        except Exception as exc:
            msg = (
                f"Booking grid did not appear within {self._timeout_ms} ms for venue "
                f"'{venue}' on {date}.  The selector '{_SELECTOR_GRID_ROOT}' was not found. "
                "The page may require login, or ClubSpark may have changed its DOM structure."
            )
            raise TimeoutError(msg) from exc

        resources = await page.query_selector_all(_SELECTOR_RESOURCE)
        if not resources:
            msg = (
                f"No courts found on the booking page for venue '{venue}' on {date}. "
                f"Expected elements matching '{_SELECTOR_RESOURCE}'."
            )
            raise ValueError(msg)

        slots = await self._parse_resources(resources)
        logger.debug("Parsed %d slot(s) for %s on %s", len(slots), venue, date)
        return slots

    async def _parse_resources(self, resources: list[ElementHandle]) -> list[RawSlot]:
        """Parse all court resources into a flat list of :class:`RawSlot` objects.

        Iterates over each ``.resource`` element, reads the court name from
        ``data-resource-name``, then delegates each ``.resource-session``
        within it to :meth:`_parse_session`.

        Args:
            resources: List of ``.resource`` element handles from the DOM.

        Returns:
            Flat list of all slots found across every court.

        """
        slots: list[RawSlot] = []
        for resource in resources:
            court_name = await resource.get_attribute(_ATTR_RESOURCE_NAME) or ""
            if not court_name:
                continue
            sessions = await resource.query_selector_all(_SELECTOR_SESSION)
            for session in sessions:
                slot = await self._parse_session(court_name, session)
                if slot is not None:
                    slots.append(slot)
        return slots

    async def _parse_session(
        self,
        court_name: str,
        session: ElementHandle,
    ) -> RawSlot | None:
        """Parse a single ``.resource-session`` element into a :class:`RawSlot`.

        Reads ``data-start-time``, ``data-end-time``, ``data-availability``,
        and ``data-session-cost`` from the element.  Start and end times are
        stored as minutes from midnight; when ``data-end-time`` is absent the
        end time is inferred as 60 minutes after the start.

        Args:
            court_name: The court name already extracted from the parent
                resource element.
            session: The ``.resource-session`` element handle to parse.

        Returns:
            A populated :class:`RawSlot`, or ``None`` if the element is
            missing the required ``data-start-time`` attribute.

        """
        start_attr = await session.get_attribute(_ATTR_START_TIME)
        if start_attr is None:
            return None

        try:
            start_time = _minutes_to_time(int(start_attr))
        except (ValueError, TypeError):
            return None

        end_attr = await session.get_attribute(_ATTR_END_TIME)
        if end_attr is not None:
            try:
                end_time = _minutes_to_time(int(end_attr))
            except (ValueError, TypeError):
                end_time = _infer_end_time(start_time)
        else:
            end_time = _infer_end_time(start_time)

        avail_attr = await session.get_attribute(_ATTR_AVAILABILITY)
        is_available = avail_attr == _AVAILABLE_VALUE

        cost_attr = await session.get_attribute(_ATTR_SESSION_COST)
        price: str | None = (cost_attr.strip() or None) if cost_attr is not None else None

        return RawSlot(
            court_name=court_name,
            start_time=start_time,
            end_time=end_time,
            is_available=is_available,
            price=price,
        )


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _minutes_to_time(minutes: int) -> datetime.time:
    """Convert a minutes-from-midnight integer to a :class:`datetime.time`.

    ClubSpark encodes slot boundaries as integers representing elapsed minutes
    since midnight (e.g. 420 → 07:00, 1020 → 17:00).

    Args:
        minutes: Non-negative integer of minutes elapsed since midnight.

    Returns:
        The corresponding :class:`datetime.time`.

    Raises:
        ValueError: If *minutes* produces an invalid time (e.g. negative or
            greater than 1439).

    """
    return datetime.time(minutes // 60, minutes % 60)


def _infer_end_time(start_time: datetime.time, duration_minutes: int = 60) -> datetime.time:
    """Derive an end time by adding *duration_minutes* to *start_time*.

    Used as a fallback when the session element does not carry an explicit
    ``data-end-time`` attribute.

    Args:
        start_time: The start time of the slot.
        duration_minutes: Duration to add in minutes (default 60).

    Returns:
        The inferred end time.

    """
    base = datetime.datetime(
        2000, 1, 1,
        start_time.hour, start_time.minute, start_time.second,
        tzinfo=datetime.UTC,
    )
    result = base + datetime.timedelta(minutes=duration_minutes)
    return result.time()
