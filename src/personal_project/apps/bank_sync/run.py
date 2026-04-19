"""Command-line entry point for bank-sync.

This module provides the CLI for interacting with the GoCardless API,
allowing users to list banks, link accounts, and sync data.
"""

from __future__ import annotations

import argparse
import logging
import sys

from personal_project.apps.bank_sync.config import BankSyncConfig
from personal_project.apps.bank_sync.service import BankSyncService

logger = logging.getLogger("bank_sync")


def main() -> int:
    """CLI entry point.

    Returns:
        Exit code (0 for success, non-zero for failure).

    """
    parser = argparse.ArgumentParser(description="Bank Sync via GoCardless")
    parser.add_argument("--config", help="Path to YAML config file")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")

    subparsers = parser.add_subparsers(dest="command", required=True)

    # List Institutions
    list_inst = subparsers.add_parser("list-banks", help="List supported banks for a country")
    list_inst.add_argument("--country", default="GB", help="Two-letter country code")

    # Get Auth Link
    auth_link = subparsers.add_parser("link", help="Generate authorization link for a bank")
    auth_link.add_argument("--bank", required=True, help="Institution ID")
    auth_link.add_argument("--redirect", default="https://localhost", help="Redirect URL")
    auth_link.add_argument("--ref", default="bank_sync_auth", help="Internal reference")

    # Sync Requisition
    sync_req = subparsers.add_parser("sync", help="Sync data for an authorized requisition")
    sync_req.add_argument("--id", required=True, help="Requisition ID")

    args = parser.parse_args()

    # Logging setup
    log_level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(level=log_level, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    config = BankSyncConfig(args.config)
    service = BankSyncService(config)

    try:
        if args.command == "list-banks":
            count = service.sync_institutions(args.country)
            print(f"Synced {count} institutions for {args.country}")

        elif args.command == "link":
            link = service.get_auth_link(args.bank, args.redirect, args.ref)
            print(f"Authorisation link: {link}")

        elif args.command == "sync":
            stats = service.sync_requisition(args.id)
            print("Sync complete:")
            print(f"  Accounts:     {stats['accounts']}")
            print(f"  Balances:     {stats['balances']}")
            print(f"  Transactions: {stats['transactions']}")

        return 0
    except Exception:
        logger.exception("Operation failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
