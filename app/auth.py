"""Bearer -> Principal resolution.

SECURITY CODE. The raw bearer lives only inside `Secret`, whose repr/str are
redacted, so an accidental f-string or log call cannot print it. The only
place the raw value is read is `Secret.reveal()`, called by the company API
client when it builds the outbound Authorization header.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import Header, HTTPException, status

from .config import settings

# Per-process key: fingerprints correlate requests within one run but cannot
# be reversed into the (low-entropy, shared-secret) tokens.
_FP_KEY = os.urandom(32)


class Secret:
    __slots__ = ("_v",)

    def __init__(self, value: str):
        self._v = value

    def reveal(self) -> str:
        return self._v

    def fingerprint(self) -> str:
        return hmac.new(_FP_KEY, self._v.encode(), hashlib.sha256).hexdigest()[:10]

    def __repr__(self) -> str:  # never print the value
        return "Secret([REDACTED])"

    __str__ = __repr__


@dataclass(frozen=True)
class Principal:
    tenant_id: str
    tenant_name: str
    companies: tuple[str, ...]  # tickers this principal may touch
    credential: Secret = field(repr=False, compare=False)

    def can_access(self, ticker: str | None) -> bool:
        return ticker is not None and ticker in self.companies


def _load_tenants(path: Path) -> dict[str, dict]:
    with path.open() as f:
        return json.load(f)


TENANTS: dict[str, dict] = _load_tenants(settings.data_dir / "tenants.json")


def known_tokens() -> list[str]:
    """Used only by the log redactor, so it can scrub exact token values."""
    return list(TENANTS.keys())


def resolve_bearer(authorization: str | None) -> Principal:
    if not authorization:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing_bearer")
    scheme, _, token = authorization.partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "malformed_authorization_header")
    entry = TENANTS.get(token)
    if entry is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "unknown_bearer")
    return Principal(
        tenant_id=entry["tenant_id"],
        tenant_name=entry["tenant_name"],
        companies=tuple(entry["companies"]),
        credential=Secret(token),
    )


def require_principal(authorization: str | None = Header(default=None)) -> Principal:
    """FastAPI dependency."""
    return resolve_bearer(authorization)
