"""
Telegram Bot API client.

Sends property listing notifications to a Telegram channel.
The bot token is read from the TELEGRAM_BOT_TOKEN environment variable.

Usage:
    async with TelegramClient(token=os.environ["TELEGRAM_BOT_TOKEN"]) as tg:
        await tg.send_listing(channel_id="@mychannel", prop=stored_property)
"""

import asyncio
import logging
import os

import httpx

from src.store.rightmove import StoredProperty

log = logging.getLogger(__name__)

_API_BASE = "https://api.telegram.org/bot{token}/{method}"

# Telegram allows ~1 msg/sec to a single chat before hitting rate limits
_MSG_DELAY = 1.1


class TelegramError(Exception):
    pass


class TelegramClient:
    def __init__(self, token: str | None = None) -> None:
        self._token = token or os.environ["TELEGRAM_BOT_TOKEN"]
        self._http = httpx.AsyncClient(timeout=15.0)

    async def __aenter__(self) -> "TelegramClient":
        return self

    async def __aexit__(self, *_) -> None:
        await self._http.aclose()

    async def close(self) -> None:
        await self._http.aclose()

    # ------------------------------------------------------------------
    # Low-level send
    # ------------------------------------------------------------------

    def _url(self, method: str) -> str:
        return _API_BASE.format(token=self._token, method=method)

    async def _post(self, method: str, payload: dict) -> dict:
        response = await self._http.post(self._url(method), json=payload)
        data = response.json()
        if not data.get("ok"):
            raise TelegramError(f"Telegram API error [{method}]: {data.get('description')}")
        return data["result"]

    async def send_message(
        self,
        chat_id: str | int,
        text: str,
        parse_mode: str = "HTML",
        disable_web_page_preview: bool = False,
    ) -> dict:
        return await self._post("sendMessage", {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": disable_web_page_preview,
        })

    # ------------------------------------------------------------------
    # Property notifications
    # ------------------------------------------------------------------

    @staticmethod
    def _format_listing(prop: StoredProperty) -> str:
        beds = f"{prop.bedrooms} bed" if prop.bedrooms else "Studio/unknown"
        price = f"£{prop.price_amount:,}" if prop.price_amount else "?"
        if prop.price_frequency:
            price += f" {prop.price_frequency}"
        listed = prop.listing_date.isoformat() if prop.listing_date else "unknown"
        found = prop.discovered_at.strftime("%d %b %H:%M UTC")

        return (
            f"🏠 <b>{prop.display_address or 'Unknown address'}</b>\n"
            f"🛏 {beds}   💰 {price}\n"
            f"📅 Listed: {listed}   🔍 Found: {found}\n"
            f"🔗 <a href=\"{prop.url}\">View on Rightmove</a>"
        )

    async def send_listing(
        self,
        chat_id: str | int,
        prop: StoredProperty,
    ) -> None:
        """Send a single property listing as a Telegram message."""
        text = self._format_listing(prop)
        await self.send_message(chat_id, text, disable_web_page_preview=True)

    async def send_listings_batch(
        self,
        chat_id: str | int,
        properties: list[StoredProperty],
        search_name: str = "",
    ) -> int:
        """
        Send a batch of new listings to a channel.
        Sends a header message, then one message per property.
        Returns the number of messages successfully sent.
        """
        if not properties:
            return 0

        n = len(properties)
        header = (
            f"🔔 <b>{n} new listing{'s' if n > 1 else ''}</b>"
            + (f" — {search_name}" if search_name else "")
        )
        await self.send_message(chat_id, header, disable_web_page_preview=True)
        await asyncio.sleep(_MSG_DELAY)

        sent = 0
        for prop in properties:
            try:
                await self.send_listing(chat_id, prop)
                sent += 1
                await asyncio.sleep(_MSG_DELAY)
            except TelegramError:
                log.exception("Failed to send listing %s", prop.rightmove_id)

        return sent
