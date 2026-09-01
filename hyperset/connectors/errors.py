"""Errors for hyperset.connectors."""

from __future__ import annotations


class ConnectorError(Exception):
    """Base class for all hyperset.connectors errors."""


class ConnectorAuthError(ConnectorError):
    """Authenticating to the source system failed."""


class BundleParseError(ConnectorError):
    """An export bundle is malformed or missing a required field."""
