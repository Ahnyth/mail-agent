"""Wrapper that keeps passwords out of logs, reprs and serialisation."""

from typing import NoReturn

_MASK = "Secret('***')"
"""What every text form of a secret shows instead of its value."""


class Secret:
    """Holds a secret value and only hands it out via :meth:`reveal`.

    Limitation: Python strings are immutable and cannot be wiped from memory reliably.
    """

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        """Keeps ``value``.

        Raises:
            ValueError: ``value`` is empty or no string.
        """
        if not isinstance(value, str) or not value:
            raise ValueError("secret_empty_or_invalid")
        self._value = value

    def reveal(self) -> str:
        """Returns the secret value; call it only where the value is really needed (e.g. the IMAP login)."""
        return self._value

    def __repr__(self) -> str:
        return _MASK

    __str__ = __repr__

    def __format__(self, format_spec: str) -> str:
        return _MASK

    def __reduce__(self) -> NoReturn:
        raise TypeError("secret_not_serializable")

    def __copy__(self) -> NoReturn:
        raise TypeError("secret_not_copyable")

    def __deepcopy__(self, memo: object) -> NoReturn:
        raise TypeError("secret_not_copyable")
