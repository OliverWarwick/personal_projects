"""ClubSpark client package.

Provides an async browser-automation client for interacting with the ClubSpark
LTA booking platform (https://clubspark.lta.org.uk). Since ClubSpark renders
its booking grid as a client-side SPA, this package uses Playwright to drive a
headless Chromium browser and scrape the rendered DOM.
"""

from personal_project.clients.clubspark.client import ClubSparkClient, RawSlot

__all__ = ["ClubSparkClient", "RawSlot"]
