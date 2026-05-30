"""Synthetic HL CSV fixtures used by parser/reconciliation tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

ACCOUNT_SUMMARY = (
    "HL Stocks & Shares ISA, , , ,\n"
    "Client Name:,Mr Test User, , ,\n"
    "Client Number:, 1234567, , ,\n"
    "Spreadsheet created at,01-04-2026 09:00, , ,\n"
    "\n"
    'Stock value:,"10,500.00", , ,\n'
    'Total cash:,"100.00", , ,\n'
    'Amount available to invest:,"100.00", , ,\n'
    'Total value:,"10,600.00", , ,\n'
    "\n"
    "Code,Stock,Units held,Price (pence),Value (\xa3),Cost (\xa3),Gain/loss (\xa3),Gain/loss (%),\n"
    '"VUAG","Vanguard Funds Plc S&P 500 UCITS ETF USD ACC (GBP) *1","100","10,500.00","10,500.00","9,000.00","1,500.00","16.67"\n'
    '"","Totals","","","10,500.00","9,000.00","1,500.00","16.67"\n'
)

PORTFOLIO_CURRENT = (
    "Portfolio Summary\n"
    "Client Name:,Mr Test User\n"
    "Client Number:,1234567\n"
    "Valuation as at,01-04-2026 09:00\n"
    "\n"
    "Trade date,Settle date,Reference,Description,Unit cost (p),Quantity,Value (\xa3)\n"
    '"15/03/2026","17/03/2026","B100000001","Vanguard Funds Plc S&P 500 UCITS ETF USD ACC (GBP) 40 @  9000.00","9000.00","40.000000","-3600.00"\n'
    '"01/03/2026","01/03/2026","INTEREST","Interest","n/a","n/a","0.10"\n'
)

# Per-tax-year file overlaps with current on B100000001 (must dedupe) and
# adds the earlier B100000000 trade.
PORTFOLIO_PRIOR = (
    "Portfolio Summary\n"
    "Client Name:,Mr Test User\n"
    "Client Number:,1234567\n"
    "Valuation as at,01-04-2026 09:00\n"
    "\n"
    "Trade date,Settle date,Reference,Description,Unit cost (p),Quantity,Value (\xa3)\n"
    '"15/03/2026","17/03/2026","B100000001","Vanguard Funds Plc S&P 500 UCITS ETF USD ACC (GBP) 40 @  9000.00","9000.00","40.000000","-3600.00"\n'
    '"01/02/2026","03/02/2026","B100000000","Vanguard Funds Plc S&P 500 UCITS ETF USD ACC (GBP) 60 @  9000.00","9000.00","60.000000","-5400.00"\n'
    '"15/01/2026","15/01/2026","CARD WEB","Opening Subscription","n/a","n/a","10000.00"\n'
)


@pytest.fixture
def hl_isa_dir(tmp_path: Path) -> Path:
    """Create a synthetic HL ISA folder layout and return its root."""
    isa = tmp_path / "ISA"
    isa.mkdir()
    (isa / "account-summary-isa.csv").write_text(ACCOUNT_SUMMARY, encoding="cp1252")
    (isa / "portfolio-summary-isa.csv").write_text(PORTFOLIO_CURRENT, encoding="cp1252")
    (isa / "portfolio-summary-isa_2526.csv").write_text(PORTFOLIO_PRIOR, encoding="cp1252")
    return tmp_path
