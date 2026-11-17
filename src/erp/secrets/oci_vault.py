"""OCI Vault provider stub.

The real client (instance principal or workload identity, sa-saopaulo-1) is an
infrastructure TODO. This stub fails closed: it never returns a hardcoded secret
and it never treats the OCID string itself as the secret value.
"""

from erp.domain.errors import SecretRefNotSupported


class OciVaultSecretsProvider:
    """Fail-closed stand-in for OCI Secrets Retrieval."""

    def get_secret(self, ref: str) -> str:
        if not ref.startswith("ocid1.vaultsecret."):
            raise SecretRefNotSupported(
                "OCI Vault provider expects a secret OCID starting with "
                f"ocid1.vaultsecret., got {ref!r}."
            )
        raise NotImplementedError(
            "OCI Vault fetch is not implemented. Wire the OCI SDK in a later "
            "change. Secret values must be read from Vault at runtime, never "
            "from source, environment samples, or the database."
        )
