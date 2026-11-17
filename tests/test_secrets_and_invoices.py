"""Secret providers fail closed. Invoice transitions do not invent tax rules."""

import hashlib
import hmac

import httpx
import pytest

from erp.config import Settings
from erp.domain.enums import InvoiceStatus
from erp.domain.errors import InvalidInvoiceTransition, SecretNotFound, SecretRefNotSupported
from erp.invoices.state_machine import transition
from erp.ops.webhook import WebhookNotifier, sign_body
from erp.secrets.local import LocalDevSecretsProvider
from erp.secrets.oci_vault import OciVaultSecretsProvider


def test_shadow_mode_defaults_to_true():
    assert Settings.model_fields["shadow_mode"].default is True


def test_local_provider_reads_env_and_files_and_rejects_ocids(monkeypatch, tmp_path):
    monkeypatch.setenv("OPS_BEARER_TOKEN", "from-env")
    provider = LocalDevSecretsProvider()
    assert provider.get_secret("env:OPS_BEARER_TOKEN") == "from-env"
    secret_file = tmp_path / "hmac.txt"
    secret_file.write_text("from-file\n", encoding="utf-8")
    assert provider.get_secret(f"file:{secret_file}") == "from-file"
    with pytest.raises(SecretNotFound):
        provider.get_secret("env:MISSING_SECRET")
    with pytest.raises(SecretRefNotSupported):
        provider.get_secret("ocid1.vaultsecret.oc1.sa-saopaulo-1.example")


def test_oci_vault_provider_is_a_fail_closed_stub():
    provider = OciVaultSecretsProvider()
    with pytest.raises(NotImplementedError):
        provider.get_secret("ocid1.vaultsecret.oc1.sa-saopaulo-1.example")
    with pytest.raises(SecretRefNotSupported):
        provider.get_secret("env:OPS_BEARER_TOKEN")


def test_invoice_state_machine_has_no_tax_side_effects():
    assert transition(InvoiceStatus.PENDING, InvoiceStatus.B2B_INPUT_ISSUED) == "B2B_INPUT_ISSUED"
    assert (
        transition(InvoiceStatus.B2B_INPUT_ISSUED, InvoiceStatus.CUSTOMER_NFE_ISSUED)
        == "CUSTOMER_NFE_ISSUED"
    )
    with pytest.raises(InvalidInvoiceTransition):
        transition(InvoiceStatus.CANCELLED, InvoiceStatus.PENDING)
    with pytest.raises(InvalidInvoiceTransition):
        transition(InvoiceStatus.CUSTOMER_NFE_ISSUED, InvoiceStatus.B2B_INPUT_ISSUED)


def test_webhook_signature_is_hmac_sha256():
    body = b'{"event_type":"dlq.retry_failed","payload":{"id":"1"}}'
    signature = sign_body("topsecret", body)
    expected = "sha256=" + hmac.new(b"topsecret", body, hashlib.sha256).hexdigest()
    assert hmac.compare_digest(signature, expected)


async def test_webhook_stub_does_not_call_http(monkeypatch):
    def explode(**kwargs):
        raise AssertionError(f"HTTP client constructed: {kwargs}")

    monkeypatch.setattr(httpx, "AsyncClient", explode)
    notifier = WebhookNotifier(secret="topsecret", url="")
    result = await notifier.send("dlq.retry_failed", {"dlq_event_id": "abc"})
    assert result["delivered"] is False
    assert result["signature"].startswith("sha256=")
