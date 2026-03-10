#!/usr/bin/env python3
"""
Open a page with Playwright, record HAR and save DOM after manual interaction.

Default outputs saved to: personal_project/data/better_com_capture/

Usage:
    python3 personal_project/scripts/capture_better.py https://better.com

Run headful (no --headless) so you can log in manually, then press Enter in the
terminal to save the page HTML and HAR file.

Features added:
- --out-dir to set where outputs are written (default: personal_project/data/better_com_capture)
- --wait-selector to wait for a CSS selector to appear before saving
- --timeout seconds to auto-save after a timeout (useful in headless runs)
- --screenshot to capture a PNG of the page
- graceful handling of Playwright errors and keyboard interrupt
"""
from pathlib import Path
import argparse
from datetime import datetime
import logging
from playwright.sync_api import sync_playwright, Error as PlaywrightError


DEFAULT_DATA_DIR = Path("personal_project") / "data" / "better_com_capture"
DEFAULT_DATA_DIR.mkdir(parents=True, exist_ok=True)


def _timestamp() -> str:
    return datetime.now().isoformat(timespec="seconds").replace(":", "-")


def build_default_paths(out_dir: Path, html_name: str | None, har_name: str | None, screenshot_name: str | None) -> tuple[Path, Path, Path | None]:
    out_dir = (out_dir or DEFAULT_DATA_DIR).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = _timestamp()
    html_file = out_dir / (html_name or f"page_snapshot_{ts}.html")
    har_file = out_dir / (har_name or f"network_capture_{ts}.har")
    screenshot_file = None
    if screenshot_name is not None:
        screenshot_file = out_dir / (screenshot_name or f"screenshot_{ts}.png")
    return html_file, har_file, screenshot_file


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="capture_better",
        description="Open a page with Playwright, record HAR and save DOM after manual interaction."
    )
    parser.add_argument("url", help="URL to open, e.g. https://better.com")
    parser.add_argument("--out-dir", type=Path, default=None, help="Directory to save outputs (default: personal_project/data/better_com_capture/)")
    parser.add_argument("--out-html", default=None, help="Output HTML filename (default: timestamped in out-dir)")
    parser.add_argument("--out-har", default=None, help="Output HAR filename (default: timestamped in out-dir)")
    parser.add_argument("--screenshot", nargs="?", const="screenshot.png", default=None, help="Save a PNG screenshot (optional filename)")
    parser.add_argument("--headless", action="store_true", help="Run headless (not recommended for manual login)")
    parser.add_argument("--wait-selector", default=None, help="CSS selector to wait for before saving (e.g. '.account-name')")
    parser.add_argument("--timeout", type=int, default=None, help="Auto-save after this many seconds (useful for headless runs). If omitted, waits for Enter key when headful.")
    return parser.parse_args()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    args = parse_args()

    out_html, out_har, screenshot_file = build_default_paths(args.out_dir, args.out_html, args.out_har, args.screenshot)

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=args.headless)
            context = browser.new_context(record_har_path=str(out_har))
            page = context.new_page()
            logging.info("Opening %s ...", args.url)
            page.goto(args.url, wait_until="networkidle")

            if args.wait_selector:
                try:
                    logging.info("Waiting for selector: %s", args.wait_selector)
                    page.wait_for_selector(args.wait_selector, timeout=(args.timeout or 30000) * 1000 if args.timeout else 30000)
                except PlaywrightError:
                    logging.warning("Timeout or error waiting for selector '%s'", args.wait_selector)

            if args.timeout and args.headless:
                logging.info("Headless mode: will auto-save after %s seconds", args.timeout)
                try:
                    page.wait_for_timeout(args.timeout * 1000)
                except KeyboardInterrupt:
                    logging.info("Interrupted by user, proceeding to save files")
            else:
                # If not headless or no timeout provided, allow manual interaction
                try:
                    logging.info("Page loaded. Interact with the page now (e.g. login).")
                    input("When finished interacting, press Enter to save HTML, HAR, and optional screenshot...")
                except KeyboardInterrupt:
                    logging.info("Interrupted by user, proceeding to save files")

            logging.info("Saving HTML to %s", out_html)
            html = page.content()
            out_html.write_text(html, encoding="utf-8")

            if screenshot_file:
                logging.info("Saving screenshot to %s", screenshot_file)
                page.screenshot(path=str(screenshot_file), full_page=True)

            # Close context/browsers to flush HAR to disk
            context.close()
            browser.close()

            logging.info("Saved HAR to: %s", out_har)
            logging.info("Saved HTML to: %s", out_html)
            if screenshot_file:
                logging.info("Saved screenshot to: %s", screenshot_file)

        return 0
    except PlaywrightError as e:
        logging.error("Playwright error: %s", e)
        return 2
    except Exception as e:
        logging.exception("Unexpected error: %s", e)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
