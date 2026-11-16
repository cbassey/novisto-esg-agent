"""
Company REST API - mock internal service for the take-home.

Bearer-scoped, tenant-isolated. The candidate's agent calls this via HTTP
with the user's Authorization: Bearer <token> header propagated as-is.
A token that doesn't own the requested ticker gets a 403.

DO NOT MODIFY. This service is part of the fixed scaffold.
"""

import json
import logging
import os
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s company_api %(message)s',
)
log = logging.getLogger("company_api")

DATA_DIR = Path(os.environ.get("DATA_DIR", "/app/data"))

with (DATA_DIR / "tenants.json").open() as f:
    TENANTS = json.load(f)  # token -> {tenant_id, tenant_name, companies: [...]}

with (DATA_DIR / "companies.json").open() as f:
    COMPANIES = json.load(f)  # ticker -> {name, sector, hq, ..., tenant_id, prior_year_disclosure}


app = FastAPI(
    title="Company API",
    description="Internal mock service - returns company facts, bearer-scoped by tenant.",
    version="1.0.0",
)

# Declared so Swagger UI shows an "Authorize" button and sends the bearer
# automatically on every Try-it-out call.
bearer_scheme = HTTPBearer(auto_error=False)


def auth(creds: HTTPAuthorizationCredentials = Depends(bearer_scheme)):
    """Resolve the bearer token to a tenant. Reject if missing / unknown."""
    if creds is None or creds.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing_or_malformed_authorization_header",
        )
    tenant = TENANTS.get(creds.credentials)
    if tenant is None:
        # Log that we saw an unknown token but never log the token itself.
        log.warning("auth rejected: unknown bearer token")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="unknown_bearer_token",
        )
    return tenant


def _check_scope(tenant, ticker):
    if ticker not in tenant["companies"]:
        log.warning(
            "cross-tenant fetch blocked: tenant=%s requested=%s",
            tenant["tenant_id"],
            ticker,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="ticker_not_in_tenant_scope",
        )


@app.get("/health")
def health():
    return {"status": "ok", "companies_loaded": len(COMPANIES)}


@app.get("/companies")
def list_companies(tenant=Depends(auth)):
    """List companies visible to this tenant."""
    out = []
    for ticker in tenant["companies"]:
        c = COMPANIES.get(ticker)
        if not c:
            continue
        out.append({
            "ticker": ticker,
            "name": c["name"],
            "sector": c["sector"],
            "hq": c["hq"],
        })
    return {"tenant_id": tenant["tenant_id"], "companies": out}


@app.get("/companies/{ticker}")
def get_company(ticker: str, tenant=Depends(auth)):
    """Return full company facts for a ticker in the caller's tenant."""
    ticker = ticker.upper()
    _check_scope(tenant, ticker)
    c = COMPANIES.get(ticker)
    if not c:
        raise HTTPException(status_code=404, detail="company_not_found")
    return {
        "ticker": ticker,
        "name": c["name"],
        "sector": c["sector"],
        "hq": c["hq"],
        "fiscal_year": c["fiscal_year"],
        "headcount": c["headcount"],
        "board_size": c["board_size"],
    }


@app.get("/companies/{ticker}/prior_year_disclosure")
def get_prior_year(ticker: str, tenant=Depends(auth)):
    """Prior-year ESG figures - useful for sanity-checking the prefill."""
    ticker = ticker.upper()
    _check_scope(tenant, ticker)
    c = COMPANIES.get(ticker)
    if not c:
        raise HTTPException(status_code=404, detail="company_not_found")
    return {
        "ticker": ticker,
        "prior_year_disclosure": c["prior_year_disclosure"],
    }
