"""Timeflux-AMTI exceptions"""

from timeflux.core.exceptions import TimefluxException


class TimefluxAmtiException(TimefluxException):
    """Exception thrown when there is a problem with the AMTI device """
    pass
