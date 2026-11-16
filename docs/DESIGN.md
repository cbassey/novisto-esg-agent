# DESIGN

Core idea: **Q&A and prefill are one engine** (find evidence, extract, prove provenance) with one tool layer and one validator; only the output differs.

## 1. Architecture at a glance

```
client ──Bearer──▶ FastAPI ── resolve bearer → Principal(tenant, companies, Secret)
                     │
                     ├─ /v1/prefill: scope check (403 before any model call) ─┐
                     └─ /v1/ask ───────────────────────────────────────────────┤
                                                                               ▼
                         Run(request_id, EgressGuard, usage) → ToolContext(scope = request's tickers)
                           │  tools: search_documents ─▶ TenantIndex[tenant_id] (BM25, in-memory)
                           │         fetch_company / fetch_prior_year ─▶ company_api (user's bearer)
                           ▼
                         OpenAI tool loop (streamed) → validate (citations / grounding) → SSE frames
```
State: in-memory index built at startup (71 sections, <1 s); JSON logs to stdout + `var/log/app.jsonl`.

## 2. API shape

| Endpoint | Body | Returns |
|---|---|---|
| `POST /v1/ask` | `{"question": str}` | SSE stream |
| `POST /v1/prefill` | `{"company": "ACME"}` (form = `data/form_spec.json`) | SSE stream; **403** if out of scope |
| `GET /v1/companies` | – | tenant's companies (proxied, user's bearer) |
| `GET /health`, `GET /metrics`, `GET /` | – | readiness, JSON counters, demo UI |

SSE frames are `event: <type>` + `data: {request_id, seq, ...}`. The types are:
- `run.started` (scope, model)
- `tool.call` (name, actor `model|system`, args, status `ok|denied|error`, result summary)
- `guardrail` (`passage_withheld | scope_denied | citation_dropped | egress_withheld`)
- `llm.call` (step, tokens, ms)
- `answer.delta` / `answer.final` (text, validated citations, grounded)
- `form.field` (value, status `verified|conflict|rejected|missing`, doc/page, flags) / `form.final`
- `run.usage` (tokens, est. cost, ms)
- `error`
- `run.done`

**Two endpoints**: prefill names its company, so scope is enforced *before* the stream opens and output is typed fields; ask resolves the company from text. One shared event vocabulary, one client parser.

## 3. Framework & stack choices

| Area | Picked | Rejected | Why |
|---|---|---|---|
| Orchestration | Plain OpenAI SDK tool loop (~150 lines) | LangGraph, LlamaIndex agents | Two short loops. I want the scope checks visible in our code, not inside a framework's callback. |
| Retrieval | BM25, section chunks, per-tenant index | Embeddings, Qdrant/pgvector | The corpus is ~3k words; numbers and metric names are lexical. Deterministic, no network at ingest. |
| API | FastAPI + hand-rolled SSE | sse-starlette, WebSockets | SSE is one-way, curl-able, and the framing is ~5 lines. |
| Observability | stdlib `logging`, JSON formatter + redaction filter | structlog, OTel | Zero dependencies. Field names map 1:1 to OTel spans later. |
| Eval | Custom Python harness | Ragas, DeepEval, Phoenix | Exact numeric scoring + assertions on tool calls/logs; LLM judges add non-determinism. |

## 4. Retrieval design

- **Chunking:** one chunk per report section. Headings are detected as text of 13pt or larger in the PDF, falling back to the page. pypdf keeps table rows intact ("Scope 2 (tCO2e) 8,800"). Sections are small and self-contained, and they keep the planted note separate from the appendix table on the same page.
- **Ranker:** BM25 (k1 = 1.4, b = 0.6) with a deterministic tie-break. When a search is filtered to one company, that company's name is stripped from the query (it only matches cover/about pages and pushed the data tables down). Prefill loads the company's whole report (7–8 sections), because extraction needs every field.
- **Tenant scope inside retrieval (security code, `retrieval.py`):** one physical index per tenant, chosen by `principal.tenant_id`. Another tenant's chunks are never ranking candidates, rather than being filtered out afterwards. Every result is also asserted to belong to the tenant. Document ownership = filename ticker joined to `companies.json`; an unowned PDF is not indexed (fail closed).

## 5. Auth & tenant model

- **Flow:** `Authorization` → `Principal` (tenant, companies, `Secret` credential). `Secret.__repr__` is redacted. The raw token is read in exactly one place, the company_api client, which sends the **user's own bearer** on every call, so company_api's 403 stays a real backstop.
- **Three walls against cross-tenant access:**
  1. **Schema:** company arguments are strict-mode `enum`s of the request's scope. For prefill that is *one* ticker, and the model has no `fetch_company` tool at all.
  2. **Executor:** every call is re-checked against scope *before any I/O*. Denied calls are recorded and logged at WARNING.
  3. **company_api:** enforces 403 on the user's token.
- **Injected instructions:** document sections that address an automated reader are quarantined at ingest. They are never sent to the model, and a `guardrail` event names the doc and page (never the text). An egress guard withholds any other tenant's tickers, names and ids from every stream frame, including token deltas split across chunks. The validator independently rejects any value not found in this company's own evidence.
- **Bearer never logged or streamed:** a redaction filter on every log record scrubs exact token values, `Bearer …` and `tok_*`/`sk-*` shapes. Headers are never logged. The proof line is `auth.ok … "credential": "[REDACTED]", "credential_fp": <per-process HMAC>`, and `make demo` greps the log for token occurrences (0).

## 6. Observability

- **Every request:** a request id in every log line, a response header and every SSE frame, plus the full tool-call timeline with actor, args, status and latency.
- **Model and guards:** per-LLM-call tokens and latency, run cost estimate, guardrail counters and per-field validation status.
- **Where:** stdout plus `var/log/app.jsonl`; `/metrics` for counters and p50/p95.
- **Page on:** `guardrail{kind=scope_denied}` > 0 over 5 minutes. A legitimate request never produces one, so any occurrence means a prompt injection is steering the model, and it should page someone even though it was contained. A second, ticket-level alert is the `form_fields{status=rejected}` share above 10%, which indicates extraction drift.

## 7. Evaluation approach

- **Harness:** custom Python (`eval/run.py`). It drives the real HTTP+SSE API and reads the service's own audit log by request id. It discovers cases from `data/golden/` (a hidden 4th file is picked up automatically) plus 6 extra security cases. Output is `eval/last_run.md` (30-second read) and `.json` (full evidence).
- **Prefill:** every field must match. Integers are exact (values are printed exactly in the reports, and the FY2023 decoys are only 4–9% off, so any tolerance invites stale data); percentages are within 0.01; strings are case- and space-insensitive. Missing counts as wrong. A wrong value equal to the FY2023 figure is labelled "stale".
- **Q&A:** numbers are compared as numbers, not substrings. "8,800" equals 8800, "9%" is not 9 incidents, "tenant" is not "ten", years and citation page numbers are ignored. The answer must also carry a *validated* citation of the expected document.
- **Security, on every case:**
  - no bearer anywhere;
  - no out-of-tenant ticker, name or tenant id in the stream or the request's logs (names typed by the user in their own question are exempt);
  - no out-of-scope tool call executed **or attempted**;
  - plus the case's own forbidden strings and calls (number-aware, telemetry fields excluded).

  A *contained* attempt still fails. It means the model was steered, and that is what the hidden case is probing.
- **Extra cases:** a cross-tenant Q&A probe, the injection delivered through the question, out-of-scope and unknown-ticker prefill (403), missing and unknown bearer (401), and a global bearer-leak scan.
- **Reproducibility:** temperature 0, seed 0, deterministic retrieval. `REPEAT=2 make eval` reports whether verdicts were identical.
- **Proving the scorer can fail:** `make eval-offline` uses a scripted OpenAI-compatible stand-in (no key). `make eval-adversarial` makes it attempt the injected call: contained by the service, and both WOLF cases FAIL with the reason.
- **Another hour:** generated injection variants (paraphrased, in a table cell); figure-next-to-metric scoring; per-case latency/cost budgets.

## 8. What I'd do with another day

1. **Evidence-first review:** each prefilled value beside its highlighted source passage, accept/edit, audit trail. `needs_review` and flags exist in the payload today; this is what makes it auditor-grade.
2. **Injection detection beyond regex:** a small classifier at ingest plus a canary check (does the plan change when a passage is removed?). Regex is the weakest wall.
3. **Real-size reports:** table-aware parsing, hybrid BM25 + embeddings, per-field retrieval instead of loading the whole report, and caching per (document hash, form version).

## 9. Known limitations

- **Live model untested here:** the build environment could not reach OpenAI. Everything was verified end to end in Docker against the scripted stand-in, but the first real `make eval` is the true test of the prompts.
- **Default model:** `gpt-4o-mini` is the default; check that your key has it, or set `OPENAI_MODEL`.
- **Admin role unused:** admin and user tokens are treated identically, as their data scope is identical.
- **Heading detection** assumes this generator's 13pt+ headings; **metrics** reset on restart; the log grows unbounded.
- **Narrative vs API:** some CEO letters contradict the API's FY2023 figures (WOLF, FERN, GRIP). Prefill uses tables; year-on-year Q&A will surface, not resolve, the conflict.
