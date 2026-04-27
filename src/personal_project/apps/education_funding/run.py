"""Command-line entry point for the education-funding application.

Provides a CLI for interacting with education and training funding data,
including pilot training costs and cadet pay-scale lookups, and for
launching the FastAPI web UI that fronts the same data.
"""

from __future__ import annotations

import argparse
import logging
import sys

from personal_project.apps.education_funding.config import EducationFundingConfig
from personal_project.apps.education_funding.service import EducationFundingService

logger = logging.getLogger("education_funding")


def _run_status(service: EducationFundingService) -> None:
    """Print the current data-layer status for the service."""
    print(f"Database: {service.config.database_url}")
    print("Education funding service ready.")


def _run_web(host: str, port: int, *, reload: bool) -> None:
    """Start the FastAPI web UI under uvicorn.

    Args:
        host: Interface to bind to (e.g. ``"127.0.0.1"``).
        port: TCP port to listen on.
        reload: When ``True``, enable uvicorn's autoreload — useful during
            local development, off by default in production-style runs.

    """
    import uvicorn  # noqa: PLC0415 - lazy import keeps CLI startup fast

    print(f"Starting education-funding web UI on http://{host}:{port}")
    uvicorn.run(
        "personal_project.apps.education_funding.web.app:app",
        host=host,
        port=port,
        reload=reload,
    )


def main() -> int:
    """CLI entry point for education-funding.

    Returns:
        Exit code (0 for success, non-zero for failure).

    """
    parser = argparse.ArgumentParser(description="Education Funding Research Tool")
    parser.add_argument("--config", help="Path to YAML config file")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")

    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("status", help="Show current data status")

    web_parser = subparsers.add_parser("web", help="Run the web UI")
    web_parser.add_argument("--host", default="127.0.0.1", help="Bind address")
    web_parser.add_argument("--port", type=int, default=8000, help="Port to listen on")
    web_parser.add_argument("--reload", action="store_true", help="Enable autoreload")

    args = parser.parse_args()

    log_level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    config = EducationFundingConfig(args.config)
    service = EducationFundingService(config)

    try:
        if args.command == "status":
            _run_status(service)
        elif args.command == "web":
            _run_web(args.host, args.port, reload=args.reload)
    except Exception:
        logger.exception("Operation failed")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
