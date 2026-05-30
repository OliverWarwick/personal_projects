"""Command-line entry point for the ibkr-sync app.

Provides subcommands to download a Flex statement to disk, sync activity
(trades + cash transactions), sync portfolio (positions + NAV snapshot), or
run both syncs in sequence.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from src.apps.ibkr_sync.config import IBKRSyncConfig
from src.apps.ibkr_sync.service import IBKRSyncService
from src.clients.ibkr import IBKRFlexClient

logger = logging.getLogger("ibkr_sync")


def _build_parser() -> argparse.ArgumentParser:
    """Build the argparse parser for the ibkr-sync CLI.

    Returns:
        A configured :class:`argparse.ArgumentParser`.

    """
    parser = argparse.ArgumentParser(description="IBKR Flex statement sync")
    parser.add_argument("--config", help="Path to YAML config file")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")

    subparsers = parser.add_subparsers(dest="command", required=True)

    download = subparsers.add_parser("download", help="Download a Flex statement to disk (no DB)")
    download.add_argument("--query-id", required=True, help="Flex Query ID")
    download.add_argument("--output", required=True, help="Path to write XML to")

    activity = subparsers.add_parser("sync-activity", help="Sync trades + cash transactions")
    activity.add_argument("--query-id", help="Override IBKR_FLEX_ACTIVITY_QUERY")
    activity.add_argument(
        "--from-file", help="Parse a saved Flex XML file instead of downloading"
    )

    portfolio = subparsers.add_parser("sync-portfolio", help="Sync positions + NAV snapshot")
    portfolio.add_argument("--query-id", help="Override IBKR_FLEX_PORTFOLIO_QUERY")
    portfolio.add_argument(
        "--from-file", help="Parse a saved Flex XML file instead of downloading"
    )

    sync_all = subparsers.add_parser("sync-all", help="Run both activity and portfolio syncs")
    sync_all.add_argument(
        "--from-file",
        help="Parse a saved Flex XML file once and persist both activity and portfolio sections",
    )

    return parser


def main() -> int:
    """Run the ibkr-sync CLI.

    Returns:
        Process exit code (0 on success, 1 on any handled failure).

    """
    args = _build_parser().parse_args()

    log_level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(
        level=log_level, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    config = IBKRSyncConfig(args.config)

    if args.command == "download":
        token = config.token
        if not token:
            logger.error("IBKR_FLEX_TOKEN is not configured")
            return 1
        return _run_download(token, args.query_id, args.output)

    return _run_sync(
        config,
        args.command,
        getattr(args, "query_id", None),
        getattr(args, "from_file", None),
    )


def _run_download(token: str, query_id: str, output: str) -> int:
    """Download a Flex statement XML payload to disk.

    Args:
        token: Flex Web Service token.
        query_id: Flex Query ID to fetch.
        output: Filesystem path to write the XML payload to.

    Returns:
        Process exit code (0 on success, 1 on failure).

    """
    try:
        xml = IBKRFlexClient(token).download_xml(query_id)
        output_path = Path(output)
        output_path.write_bytes(xml)
    except Exception:
        logger.exception("download failed")
        return 1
    print(f"Wrote {len(xml)} bytes to {output_path}")
    return 0


def _run_sync(
    config: IBKRSyncConfig,
    command: str,
    query_id: str | None,
    from_file: str | None = None,
) -> int:
    """Run a database-backed sync subcommand.

    Args:
        config: Loaded :class:`IBKRSyncConfig`.
        command: One of ``sync-activity``, ``sync-portfolio``, ``sync-all``.
        query_id: Optional per-invocation query ID override.
        from_file: Optional saved Flex XML path to parse instead of downloading.

    Returns:
        Process exit code (0 on success, 1 on failure).

    """
    try:
        service = IBKRSyncService(config)

        if command == "sync-activity":
            stats = service.sync_activity(query_id)
            print("Activity sync complete:")
            print(f"  Trades:            {stats['trades']}")
            print(f"  Cash transactions: {stats['cash_transactions']}")

        elif command == "sync-portfolio":
            stats = service.sync_portfolio(query_id)
            print("Portfolio sync complete:")
            print(f"  Positions: {stats['positions']}")
            print(f"  Snapshots: {stats['snapshots']}")

        elif command == "sync-all":
            activity_stats = service.sync_activity()
            portfolio_stats = service.sync_portfolio()
            print("Full sync complete:")
            print(f"  Trades:            {activity_stats['trades']}")
            print(f"  Cash transactions: {activity_stats['cash_transactions']}")
            print(f"  Positions:         {portfolio_stats['positions']}")
            print(f"  Snapshots:         {portfolio_stats['snapshots']}")

    except Exception:
        logger.exception("ibkr-sync failed")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
