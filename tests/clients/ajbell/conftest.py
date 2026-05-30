"""Synthetic AJ Bell CSV fixtures used by parser/reconciliation tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

# UTF-8 BOM-prefixed snapshot. Headers mirror the real export.
PORTFOLIO_CSV = (
    "\ufeffInvestment,Quantity,Price,Value (\u00a3),Cost (\u00a3),Change (\u00a3),Date,Time\n"
    '"iShares S&P 500 GBP Hedged ETF Acc (LSE:IGUS)","100","171.54","17,154.00","15,000.00","2,154.00","25-May-26","17:54"\n'
    '"Cash GBP","","","72.47","","","25-May-26","17:54"\n'
)

# Two purchases on IGUS plus a distribution row (quantity 0 / cash only) and
# a fully-closed legacy position to exercise unmatched_trade_descriptions.
TRANSACTION_CSV = (
    "\ufeffDate,Transaction,Description,Quantity,Price,Amount (GBP),Reference\n"
    '"07/04/2026","Purchase","iShares S&P 500 GBP Hedged ETF Acc","60","100.00","-6000.00","TXN1"\n'
    '"01/02/2026","Purchase","iShares S&P 500 GBP Hedged ETF Acc","40","125.00","-5000.00","TXN2"\n'
    '"15/01/2026","Accumulation Distribution","iShares S&P 500 GBP Hedged ETF Acc","0","","10.00","TXN3"\n'
    '"01/06/2025","Sale","Vanguard LifeStrategy 80 Acc","100","150.00","15000.00","TXN4"\n'
)


@pytest.fixture
def ajbell_dir(tmp_path: Path) -> Path:
    """Create a synthetic AJ Bell export layout and return its root."""
    (tmp_path / "portfolio-ABBXDDL-Lifetime ISA.csv").write_text(
        PORTFOLIO_CSV, encoding="utf-8",
    )
    (tmp_path / "transactionhistory.csv").write_text(
        TRANSACTION_CSV, encoding="utf-8",
    )
    return tmp_path
