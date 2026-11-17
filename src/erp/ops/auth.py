"""Bearer check for /ops. The expected token is loaded from the secrets provider."""

from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request

from erp.secrets.base import SecretsProvider
from erp.domain.errors import SecretNotFound


def tokens_match(provided: str, expected: str) -> bool:
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


async def require_ops_bearer(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    if authorization is None or " " not in authorization:
        _unauthorized()
    scheme, token = authorization.split(" ", 1)
    if scheme.lower() != "bearer" or not token.strip():
        _unauthorized()
    secrets: SecretsProvider = request.app.state.secrets
    ref = request.app.state.settings.ops_bearer_token_ref
    try:
        expected = secrets.get_secret(ref)
    except SecretNotFound:
        _unauthorized()
    if not tokens_match(token.strip(), expected):
        _unauthorized()


def _unauthorized() -> None:
    raise HTTPException(
        status_code=401,
        detail="Unauthorized",
        headers={"WWW-Authenticate": "Bearer"},
    )


OpsAuth = Depends(require_ops_bearer)
