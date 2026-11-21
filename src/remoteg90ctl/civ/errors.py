# SPDX-License-Identifier: GPL-3.0-or-later
"""Exceptions raised by the CI-V codec."""


class CivError(ValueError):
    """Base class for every error the CI-V codec raises on bad input."""


class CivParseError(CivError):
    """A byte sequence is not a well-formed CI-V frame."""


class BcdError(CivError):
    """A value cannot be encoded as, or decoded from, packed BCD."""
