"""SQLAlchemy models for bank account and transaction data.

This module defines the database schema for storing financial institutions,
accounts, balances, and transactions.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

if TYPE_CHECKING:
    from collections.abc import list


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy models."""


class Institution(Base):
    """Financial institution model.

    Represents a bank supported by the GoCardless (Nordigen) API.

    Attributes:
        id: The institution ID (e.g., "SANDBOXFINANCE_SFIN0000").
        name: The display name of the institution.

    """

    __tablename__ = "institutions"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))

    accounts: Mapped[list[Account]] = relationship(back_populates="institution")


class Account(Base):
    """Bank account model.

    Represents a bank account fetched via GoCardless.

    Attributes:
        id: The unique account ID from GoCardless.
        iban: The International Bank Account Number.
        institution_id: Foreign key to the institution.
        name: Account holder name or account nickname.
        currency: Three-letter currency code (e.g., "GBP").

    """

    __tablename__ = "accounts"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    iban: Mapped[str | None] = mapped_column(String(34))
    institution_id: Mapped[str] = mapped_column(ForeignKey("institutions.id"))
    name: Mapped[str | None] = mapped_column(String(255))
    currency: Mapped[str] = mapped_column(String(3))

    institution: Mapped[Institution] = relationship(back_populates="accounts")
    balances: Mapped[list[Balance]] = relationship(back_populates="account")
    transactions: Mapped[list[Transaction]] = relationship(back_populates="account")


class Balance(Base):
    """Account balance snapshot model.

    Attributes:
        id: Primary key.
        account_id: Foreign key to the account.
        amount: The balance amount as a decimal string.
        date: The timestamp of the balance snapshot.

    """

    __tablename__ = "balances"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    amount: Mapped[str] = mapped_column(String(50))
    date: Mapped[datetime] = mapped_column(DateTime)

    account: Mapped[Account] = relationship(back_populates="balances")


class Transaction(Base):
    """Financial transaction model.

    Attributes:
        id: The unique transaction ID from GoCardless.
        account_id: Foreign key to the account.
        amount: The transaction amount as a decimal string.
        description: The transaction description or narrative.
        date: The booking date of the transaction.
        category: Optional category or MCC code.

    """

    __tablename__ = "transactions"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    amount: Mapped[str] = mapped_column(String(50))
    description: Mapped[str] = mapped_column(String(500))
    date: Mapped[datetime] = mapped_column(DateTime)
    category: Mapped[str | None] = mapped_column(String(100))

    account: Mapped[Account] = relationship(back_populates="transactions")
