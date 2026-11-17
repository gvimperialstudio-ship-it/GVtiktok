"""Pluggable secret resolution. Implementations return values; callers pass references."""

from typing import Protocol


class SecretsProvider(Protocol):
    def get_secret(self, ref: str) -> str:
        """Resolve a secret reference to its current value.

        ``ref`` is an OCID, env name, or file path selector. Implementations must
        not log the returned value.
        """
        ...
