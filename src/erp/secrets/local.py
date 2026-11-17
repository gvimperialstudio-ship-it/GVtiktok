"""Local development provider.

Resolves ``env:VAR`` and ``file:/path`` only. It refuses OCIDs so a developer
cannot confuse a reference with a secret value. Do not use this provider in OCI.
"""

import os
from pathlib import Path

from erp.domain.errors import SecretNotFound, SecretRefNotSupported


class LocalDevSecretsProvider:
    """Read secrets from the process environment or local files."""

    def get_secret(self, ref: str) -> str:
        if ref.startswith("env:"):
            key = ref.removeprefix("env:")
            if not key or key not in os.environ or os.environ[key] == "":
                raise SecretNotFound(ref)
            return os.environ[key]
        if ref.startswith("file:"):
            path = Path(ref.removeprefix("file:")).expanduser()
            if not path.is_file():
                raise SecretNotFound(ref)
            value = path.read_text(encoding="utf-8").strip()
            if not value:
                raise SecretNotFound(ref)
            return value
        raise SecretRefNotSupported(
            "Local dev provider only resolves env: and file: references, "
            f"got {ref!r}. OCIDs require the oci_vault provider."
        )
