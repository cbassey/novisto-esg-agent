"""HTTP API. Endpoints and the SSE event contract are documented in docs/DESIGN.md."""

from __future__ import annotations

import json
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from .agent import Deps, run_ask, run_prefill
from .auth import Principal, require_principal
from .company_client import CompanyApiError, CompanyClient
from .config import settings
from .ingest import ingest_corpus
from .llm import LLM
from .logs import event, request_id_var, setup_logging
from .metrics import metrics
from .retrieval import TenantIndex

STATIC = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    chunks = ingest_corpus(settings.data_dir)
    companies = json.loads((settings.data_dir / "companies.json").read_text())
    app.state.deps = Deps(
        index=TenantIndex(chunks),
        company=CompanyClient(),
        llm=LLM(),
        company_names={t: c["name"] for t, c in companies.items()},
        form_spec=json.loads((settings.data_dir / "form_spec.json").read_text()),
    )
    event("startup", chunks=len(chunks), tenants=app.state.deps.index.stats(), model=settings.openai_model)
    yield
    await app.state.deps.company.aclose()


app = FastAPI(title="Novisto ESG agent", version="0.1.0", lifespan=lifespan)

SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def principal_dep(p: Principal = Depends(require_principal)) -> Principal:
    # The one log line that proves the bearer is handled but never written.
    event("auth.ok", tenant_id=p.tenant_id, credential="[REDACTED]", credential_fp=p.credential.fingerprint())
    return p


@app.middleware("http")
async def access_log(request: Request, call_next):
    # One id per request, shared by auth.ok, every run/tool log line and the stream.
    rid = request_id_var.set(uuid.uuid4().hex[:16])
    t0 = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id_var.get() or ""
    # Path + status only: headers (Authorization) are never logged.
    event("http.request", method=request.method, path=request.url.path, status=response.status_code,
          ms=round((time.perf_counter() - t0) * 1000, 1))
    metrics.inc("http_requests", path=request.url.path, status=response.status_code)
    request_id_var.reset(rid)
    return response


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=1000)


class PrefillRequest(BaseModel):
    company: str = Field(min_length=2, max_length=12, description="Ticker, e.g. ACME")


@app.get("/health")
async def health():
    deps: Deps = app.state.deps
    endpoint = urlparse(settings.openai_base_url).hostname if settings.openai_base_url else "api.openai.com"
    return {"status": "ok", "chunks_by_tenant": deps.index.stats(), "model": deps.llm.model, "llm_endpoint": endpoint,
            "llm_key_configured": deps.llm.configured}


@app.get("/metrics")
async def get_metrics():
    return metrics.snapshot()


@app.get("/v1/companies")
async def list_companies(principal: Principal = Depends(principal_dep)):
    try:
        return await app.state.deps.company.list_companies(principal)
    except CompanyApiError as e:
        raise HTTPException(502 if e.status >= 500 else e.status, f"company_api_{e.status}") from None


@app.post("/v1/ask")
async def ask(body: AskRequest, principal: Principal = Depends(principal_dep)):
    return StreamingResponse(run_ask(app.state.deps, principal, body.question),
                             media_type="text/event-stream", headers=SSE_HEADERS)


@app.post("/v1/prefill")
async def prefill(body: PrefillRequest, principal: Principal = Depends(principal_dep)):
    ticker = body.company.strip().upper()
    if not principal.can_access(ticker):
        # Refused before any model call or stream. Same answer for "exists in
        # another tenant" and "does not exist", so scope cannot be probed.
        event("scope.denied", tenant_id=principal.tenant_id, endpoint="prefill")
        metrics.inc("guardrail", kind="scope_denied")
        raise HTTPException(403, "company_not_in_scope")
    return StreamingResponse(run_prefill(app.state.deps, principal, ticker),
                             media_type="text/event-stream", headers=SSE_HEADERS)


@app.get("/")
async def ui():
    return FileResponse(STATIC / "index.html")


@app.exception_handler(HTTPException)
async def http_error(_: Request, exc: HTTPException):
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code)
