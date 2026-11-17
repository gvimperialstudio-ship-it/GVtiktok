"""Secrets provider factory."""

from erp.secrets.base import SecretsProvider
from erp.secrets.local import LocalDevSecretsProvider
from erp.secrets.oci_vault import OciVaultSecretsProvider


def build_secrets_provider(kind: str) -> SecretsProvider:
    if kind == "local":
        return LocalDevSecretsProvider()
    if kind == "oci_vault":
        return OciVaultSecretsProvider()
    raise ValueError(f"Unknown secrets provider {kind!r}. Expected local or oci_vault.")
