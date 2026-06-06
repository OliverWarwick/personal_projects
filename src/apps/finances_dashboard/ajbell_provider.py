"""Bridge between the AJ Bell CSV loader and the IBKR-shaped dashboard.

Mirrors :mod:`hl_provider`: each AJ Bell account becomes a synthesised
``FlexStatement`` so the renderer can show holdings and trades alongside
IBKR and HL positions.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from decimal import Decimal
from typing import TYPE_CHECKING

from src.apps.finances_dashboard._realized import (
    TradeLeg,
    compute_realized_detailed,
)
from src.apps.finances_dashboard._reconcile import inject_opening_balances
from src.clients.ajbell.loader import discover_accounts

if TYPE_CHECKING:
    from src.clients.ajbell.loader import AJBellAccount

logger = logging.getLogger(__name__)

# Account-id prefix used in the dashboard config and URL to flag an AJ
# Bell account. ``_resolve`` checks this prefix before falling back to
# the Flex-XML lookup.
AJBELL_ACCOUNT_PREFIX = "AJB-"

_SLUG_RE = re.compile(r"[^A-Za-z0-9]+")


def ajbell_account_id(account_id: str) -> str:
    """Return the dashboard account id for an AJ Bell account number."""
    return f"{AJBELL_ACCOUNT_PREFIX}{account_id}"


def _add(elem: ET.Element, tag: str, **attrs: str) -> ET.Element:
    """Append a child element with the given string attributes."""
    return ET.SubElement(elem, tag, attrs)


def _symbol_for(investment: str, ticker: str | None) -> str:
    """Return a dashboard symbol: the exchange ticker if any, else a slug."""
    if ticker:
        return ticker
    slug = _SLUG_RE.sub("-", investment).strip("-").upper()
    return slug[:24] or "OEIC"


def synthesize_flex_statement(account: AJBellAccount) -> ET.Element:
    """Build a ``FlexStatement`` element mirroring an AJ Bell account.

    Args:
        account: Fully-loaded AJ Bell account (snapshot + ledger slice).

    Returns:
        ``FlexStatement`` with ``OpenPosition`` children per holding plus
        ``Trade`` children for PURCHASE/SALE rows, using the snapshot
        ticker when AJ Bell publishes one and a slug otherwise.

    """
    stmt = ET.Element(
        "FlexStatement",
        {
            "accountId": ajbell_account_id(account.portfolio.account_id),
            "currency": "GBP",
        },
    )

    code_by_name: dict[str, str] = {}
    for h in account.portfolio.holdings:
        code = _symbol_for(h.investment, h.ticker)
        code_by_name[h.investment.casefold()] = code
        cost_per_unit = (h.cost_gbp / h.quantity) if h.quantity else Decimal(0)
        _add(
            stmt,
            "OpenPosition",
            levelOfDetail="SUMMARY",
            symbol=code,
            listingExchange="AJB",
            currency="GBP",
            position=str(h.quantity),
            costBasisPrice=str(cost_per_unit),
            markPrice=str(h.price_gbp),
            positionValue=str(h.value_gbp),
            fxRateToBase="1",
            fifoPnlUnrealized=str(h.change_gbp),
            description=h.investment,
            assetCategory="AJB",
        )

    # Iterate the broker-wide ledger so closed positions (sold long ago)
    # still appear on the timeline. Single-portfolio AJ Bell exports
    # carry one account per file, so cross-account attribution is moot.
    eligible = [
        txn for txn in account.all_transactions
        if txn.quantity is not None and txn.price_gbp is not None
    ]
    legs = [
        TradeLeg(
            order=txn.trade_date.strftime("%Y%m%d"),
            symbol=(
                code_by_name.get(txn.description.casefold())
                or _symbol_for(txn.description, None)
            ),
            quantity=txn.quantity,  # type: ignore[arg-type]
            unit_price=txn.price_gbp,  # type: ignore[arg-type]
        )
        for txn in eligible
    ]
    realized = compute_realized_detailed(legs)
    for idx, (txn, leg) in enumerate(zip(eligible, legs, strict=True)):
        assert txn.quantity is not None  # noqa: S101  (narrow for type-checker)
        assert txn.price_gbp is not None  # noqa: S101
        proceeds = -txn.quantity * txn.price_gbp
        attrs = {
            "levelOfDetail": "EXECUTION",
            "symbol": leg.symbol,
            "currency": "GBP",
            "tradeDate": leg.order,
            "quantity": str(txn.quantity),
            "tradePrice": str(txn.price_gbp),
            "proceeds": str(proceeds),
            "fxRateToBase": "1",
            "openCloseIndicator": "O" if txn.quantity > 0 else "C",
        }
        if idx in realized:
            pnl, avg = realized[idx]
            attrs["fifoPnlRealized"] = str(pnl)
            # origTradePrice surfaces in the Closed Positions table as
            # "Open Px"; without it, AJ Bell closes render with 0.
            attrs["origTradePrice"] = str(avg)
        _add(stmt, "Trade", **attrs)

    # AJ Bell ISA / Dealing are non-margin; reconcile the synthesised cash
    # trace to the broker snapshot's cash balance and plug any negative dip
    # from missing early deposits.
    inject_opening_balances(
        stmt,
        snapshot_holdings=[
            (_symbol_for(h.investment, h.ticker), h.quantity, h.cost_gbp)
            for h in account.portfolio.holdings
        ],
        target_final_cash=Decimal(getattr(account.portfolio, "cash_gbp", 0) or 0),
    )

    return stmt


def load_ajbell_statements() -> dict[str, ET.Element]:
    """Load every AJ Bell account and return ``{account_id: FlexStatement}``.

    Returns an empty mapping when ``AJBELL_DATA_DIR`` is unset or the
    directory is missing, so the dashboard still works without AJ Bell
    data present.
    """
    try:
        accounts = discover_accounts()
    except FileNotFoundError:
        logger.info("AJ Bell data dir not found; skipping AJ Bell accounts")
        return {}
    return {
        ajbell_account_id(a.portfolio.account_id): synthesize_flex_statement(a)
        for a in accounts
    }
