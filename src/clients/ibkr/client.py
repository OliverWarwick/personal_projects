"""Synchronous client for the Interactive Brokers Flex Web Service.

This module provides :class:`IBKRFlexClient`, a thin wrapper around the
third-party ``ibflex`` library. The Flex Web Service uses a two-step flow:
the client first hits ``SendRequest`` with a token and a pre-configured
Flex Query ID to obtain a reference code, then polls ``GetStatement`` until
the statement is ready. ``ibflex.client.download`` performs both steps
internally and returns the resulting XML payload as bytes; this wrapper
exposes that flow plus typed parsing into :class:`FlexQueryResponse`.

Typical usage:
    client = IBKRFlexClient(token="...")
    response = client.download_and_parse(query_id="123456")
    for trade in response.FlexStatements[0].Trades:
        ...
"""

import logging

from ibflex import client as ibflex_client
from ibflex import parser as ibflex_parser
from ibflex.Types import FlexQueryResponse

logger = logging.getLogger(__name__)


class IBKRFlexClient:
    """Synchronous client for the Interactive Brokers Flex Web Service.

    The client wraps :mod:`ibflex` to download Flex statement XML and parse
    it into the typed :class:`ibflex.Types.FlexQueryResponse` model. The
    Flex Web Service token is configured by the user in IBKR Account
    Management and authorises access to one or more pre-defined Flex
    Queries identified by their numeric query ID.

    Attributes:
        _token: The Flex Web Service token used to authenticate downloads.
            This value is treated as a secret and is never logged.

    """

    def __init__(self, token: str) -> None:
        """Initialise the client with a Flex Web Service token.

        Args:
            token: The Flex Web Service token generated in IBKR Account
                Management. Used to authenticate every download request.

        """
        self._token = token

    def download_xml(self, query_id: str) -> bytes:
        """Download the raw Flex statement XML for a query.

        Delegates to :func:`ibflex.client.download`, which performs the
        two-step Flex Web Service flow: it issues ``SendRequest`` with the
        configured token and the supplied query ID, then polls
        ``GetStatement`` until the statement is ready. The call may block
        for several seconds while ibflex polls IBKR's ``GetStatement``
        endpoint for the prepared statement.

        Args:
            query_id: The numeric Flex Query ID configured in IBKR Account
                Management. Identifies which pre-defined statement to run.

        Returns:
            The Flex statement payload as raw XML bytes, exactly as
            returned by IBKR.

        Raises:
            ibflex.client.ResponseCodeError: If IBKR returns an error
                response code at either step of the flow.
            ibflex.client.BadResponseError: If the Flex Web Service
                response is malformed.

        """
        logger.debug("Downloading IBKR Flex statement for query_id=%s", query_id)
        return ibflex_client.download(self._token, query_id)

    @staticmethod
    def parse_xml(xml: bytes | str) -> FlexQueryResponse:
        """Parse Flex statement XML into a typed response model.

        Delegates to :func:`ibflex.parser.parse`, which accepts either a
        ``bytes`` payload (as returned by :meth:`download_xml`) or a path
        to an XML file on disk. Both inputs are passed through unchanged;
        ibflex performs the appropriate dispatch internally. Exposed as a
        staticmethod so callers can parse a saved XML file without holding
        a Flex Web Service token.

        Args:
            xml: Either the raw XML bytes returned by :meth:`download_xml`
                or a filesystem path (as ``str``) pointing at a saved
                Flex statement XML file.

        Returns:
            The parsed :class:`ibflex.Types.FlexQueryResponse`, with all
            statements, trades, positions, and cash transactions decoded
            into typed dataclasses.

        Raises:
            ibflex.parser.FlexParserError: If the XML is not a valid Flex
                statement or cannot be decoded into the typed model.

        """
        return ibflex_parser.parse(xml)  # pyright: ignore[reportUnknownMemberType]

    def download_and_parse(self, query_id: str) -> FlexQueryResponse:
        """Download a Flex statement and parse it in one call.

        Convenience method that chains :meth:`download_xml` and
        :meth:`parse_xml` for the common case where the caller wants the
        typed response directly and does not need to retain the raw XML.

        Args:
            query_id: The numeric Flex Query ID configured in IBKR Account
                Management.

        Returns:
            The parsed :class:`ibflex.Types.FlexQueryResponse` for the
            requested Flex Query.

        Raises:
            ibflex.client.ResponseCodeError: If IBKR returns an error
                response code while downloading.
            ibflex.client.BadResponseError: If the Flex Web Service
                response is malformed.
            ibflex.parser.FlexParserError: If the downloaded XML cannot
                be parsed into the typed model.

        """
        return self.parse_xml(self.download_xml(query_id))
