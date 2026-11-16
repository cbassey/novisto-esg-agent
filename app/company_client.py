"""Client for the internal company API. Propagates the caller's bearer as-is.

The service never holds credentials of its own for this API: every call is
made *as the user*, so company_api's own 401/403 remains a real backstop.
"""

from __future__ import annotations

import re

import httpx

from .auth import Principal
from .config import settings

TICKER_RE = re.compile(r"^[A-Z]{2,6}$")


class CompanyApiError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(f"{status} {detail}")
        self.status = status
        self.detail = detail


class CompanyClient:
    def __init__(self, base_url: str | None = None):
        self._http = httpx.AsyncClient(base_url=base_url or settings.company_api_url, timeout=5.0)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _get(self, principal: Principal, path: str) -> dict:
        headers = {"Authorization": f"Bearer {principal.credential.reveal()}"}
        try:
            r = await self._http.get(path, headers=headers)
        except httpx.HTTPError as e:  # network: surface, never retry blindly
            raise CompanyApiError(503, f"company_api_unreachable:{type(e).__name__}") from None
        if r.status_code != 200:
            try:
                detail = r.json().get("detail", "")
            except ValueError:
                detail = ""
            raise CompanyApiError(r.status_code, str(detail))
        return r.json()

    async def get_company(self, principal: Principal, ticker: str) -> dict:
        _require_ticker(ticker)
        return await self._get(principal, f"/companies/{ticker}")

    async def get_prior_year(self, principal: Principal, ticker: str) -> dict:
        _require_ticker(ticker)
        return await self._get(principal, f"/companies/{ticker}/prior_year_disclosure")

    async def list_companies(self, principal: Principal) -> dict:
        return await self._get(principal, "/companies")


def _require_ticker(ticker: str) -> None:
    # Strict shape check: no whitespace, slashes or newlines reach the URL or
    # company_api's logs (its log line interpolates the raw ticker).
    if not isinstance(ticker, str) or not TICKER_RE.fullmatch(ticker):
        raise CompanyApiError(400, "invalid_ticker_shape")
