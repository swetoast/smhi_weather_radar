"""Exceptions for SMHI Weather Radar."""


class SmhiRadarError(Exception):
    """Base error for the integration."""


class SmhiRadarConnectionError(SmhiRadarError):
    """Raised when SMHI cannot be reached."""


class SmhiRadarDataError(SmhiRadarError):
    """Raised when SMHI returns unusable data."""
