# DESIGN

**The core idea.** Answering a question and prefilling a form are the same job: find evidence in the reports, pull out a figure, prove where it came from. So both share one set of tools, one set of security checks and one validator. Only the output differs.

## 1. Architecture at a glance

```
client ──Bearer──▶ FastAPI ── token → Principal (tenant, its companies, token kept secret)
                     ├─ POST /v1/prefill: company not yours → 403 before any model call ─┐
                     └─ POST /v1/ask ────────────────────────────────────────────────────┤
                                                                                         ▼
                        Run per request (request id, egress guard, cost)
                          │ search_documents ─▶ this tenant's own BM25 index (in memory)
                          │ fetch_company / fetch_prior_year ─▶ company_api, with the user's token
                          ▼
                        OpenAI tool loop, streamed → check each figure against its source → SSE
```

The index is built at startup (71 report sections, under a second). Logs are JSON lines on stdout and in `var/log/app.jsonl`.

## 2. API shape

| Endpoint | Body | Returns |
|---|---|---|
| `POST /v1/ask` | `{"question": str}` | event stream |
| `POST /v1/prefill` | `{"company": "ACME"}` (form = `data/form_spec.json`) | event stream, or **403** if the company is not yours |
| `GET /v1/companies` | none | your tenant's companies |
| `GET /health`, `/metrics`, `/` | none | readiness, counters, demo UI |

Server-Sent Events: `event: <type>` plus a JSON body that always has `request_id` and `seq`.

| Event | What it carries |
|---|---|
| `run.started` | your companies, the model |
| `tool.call` | who called (model or system), arguments, `ok`/`denied`/`error`, a summary of what came back |
| `guardrail` | `passage_withheld` (injection found), `scope_denied` (cross-tenant call blocked), `citation_dropped`, `figure_unsupported`, `egress_withheld` |
| `llm.call` | tokens and time per model call |
| `answer.delta` / `answer.final` | the streaming text; then report citations, company_api citations, a verdict per figure, `grounded` |
| `form.field` / `form.final` | value, `verified`/`conflict`/`rejected`/`missing`, source page |
| `run.usage`, `error`, `run.done` | tokens, cost, time |

**Two endpoints**, because a prefill names its company up front, so scope is checked before the stream opens and the reply is a typed form. A question names its company in free text. Both share one event vocabulary, so the client has one parser.

## 3. Framework & stack choices

| Area | Picked | Rejected | Why |
|---|---|---|---|
| Agent loop | Plain OpenAI SDK (~150 lines) | LangGraph, LlamaIndex | Two short loops. The scope checks belong in our code where a reviewer sees them, not in a framework callback. |
| Retrieval | BM25, one index per tenant | Embeddings, Qdrant, pgvector | ~3,000 words of corpus, and questions turn on exact numbers and metric names. Exact, repeatable, no network. |
| API | FastAPI, hand-written SSE | sse-starlette, WebSockets | One-way, works with curl, five lines of framing. |
| Logs | stdlib `logging`, JSON + redaction filter | structlog, OpenTelemetry | No dependencies. Fields map 1:1 to OTel spans later. |
| Eval | Custom Python harness | Ragas, DeepEval, Phoenix | Exact number scoring and checks on tool calls and logs. An LLM judge would make verdicts vary. |

## 4. Retrieval design

- **Chunks:** one per report section (headings are 13pt+ text, falling back to the page). Table rows stay whole ("Scope 2 (tCO2e) 8,800"), and the planted injection note stays apart from the data table on its page.
- **Ranking:** BM25 with a fixed tie-break, so results never reorder. In a one-company search the company's name is dropped from the query, or the cover page outranks the data tables. Prefill loads the whole report (7–8 sections) because it needs every field.
- **Tenant scope (security code, `retrieval.py`):** each tenant has its own physical index, picked from the token's tenant. Another tenant's text is never a candidate, rather than filtered out afterwards, and every result is checked again. Ownership comes from the filename ticker matched against `companies.json`; a report with no known owner is not indexed.

## 5. Auth & tenant model

- **Token flow:** `Authorization` → `Principal` (tenant, companies, token in a `Secret` that prints as `[REDACTED]`). Only the company_api client reads the raw token, and it sends **the user's own token**, so company_api's 403 stays a real backstop.
- **Three walls against cross-tenant access:**
  1. **Schema:** company arguments are strict lists of this request's tickers. Prefill gets one ticker and no `fetch_company` tool.
  2. **Tool runner:** every call is re-checked against scope before any I/O. Denials are recorded and logged as warnings.
  3. **company_api:** 403 on the user's token.
- **Injection:** report sections that give orders to an automated reader are quarantined at load time. The model never sees them, and a `guardrail` event names the page, not the text. An egress guard strips other tenants' tickers, names and ids from every event, including names split across stream chunks.
- **Every figure is checked against its source.** Prefill rejects any value not in its cited passage. Ask checks every number in the answer:
  - cited to a report page → it must be in that retrieved passage;
  - from company_api → it must be cited as `[company_api ACME]` and be a value company_api returned in this request;
  - a difference or % change → recomputed from the cited figures;
  - anything else → `figure_unsupported` guardrail and `grounded: false`.

  A real run prompted this: an answer put company_api's FY2023 figure under a report citation and was still marked grounded.
- **Token never logged or streamed:** a filter on every log record removes token values, `Bearer …` and `tok_*`/`sk-*` shapes, and headers are never logged. Proof line: `auth.ok … "credential": "[REDACTED]", "credential_fp": <hash>`. `make demo` greps the log for tokens and finds none.

## 6. Observability

- **Per request:** a request id in every log line, response header and event, plus the full tool-call timeline (who, arguments, outcome, time).
- **Model and guards:** tokens and time per model call, cost per run, counts per guardrail kind, status per form field. On stdout, `var/log/app.jsonl` and `/metrics` (counters, p50/p95).
- **Page on** `guardrail{kind=scope_denied}` > 0 in 5 minutes. A normal request never triggers it, so one means an injection is steering the model, even though it was blocked. **Ticket on** `rejected` form fields above 10% (extraction drift) or a rise in `figure_unsupported` (answers claiming more than their sources).

## 7. Evaluation approach

- **Harness:** custom Python (`eval/run.py`). It calls the real API, reads the stream, then reads the service's own log for that request id. It runs every file in `data/golden/` (so a hidden 4th case runs too) plus 6 security cases of its own. Output: `eval/last_run.md` (30-second read) and `.json` (full evidence).
- **Prefill:** every field must match. Whole numbers exactly, because the FY2023 decoys in company_api are only 4–9% off and any tolerance lets stale data pass. Percentages within 0.01, text ignoring case and spacing. Missing counts as wrong; a wrong value equal to FY2023 is labelled "stale".
- **Q&A:** numbers compared as numbers: "8,800" equals 8800, but "9%" is not 9 incidents and "tenant" does not contain "ten". The answer must also carry a checked citation of the expected report.
- **Security, on every case:** no token anywhere; no other tenant's ticker, name or id in the stream or logs (unless the user typed it); no out-of-scope tool call run **or attempted**; plus each case's forbidden strings and calls. A blocked attempt still fails: the model was steered, which is what the hidden case probes.
- **Extra cases:** cross-tenant question, injection sent in the question, prefill for another tenant's or an unknown company (403), missing and unknown token (401), a full-log token scan.
- **Repeatable:** temperature 0, seed 0, fixed retrieval. `REPEAT=2 make eval` reports whether verdicts matched.
- **The scorer can fail:** `make eval-offline` runs against a scripted OpenAI stand-in (no key). `make eval-adversarial` makes it attempt the injected call; it is blocked, and both WOLF cases fail with the reason.
- **Another hour:** reworded injections and ones hidden in table cells; checking the figure sits beside the right metric; latency and cost budgets per case.

## 8. What I'd do with another day

1. **Evidence-first review:** each prefilled value beside its highlighted source, with accept/edit and an audit trail. The payload already has `needs_review` and flags.
2. **Better injection detection:** regex is the weakest wall. Add a small classifier at load time and a canary check (does the plan change when a passage is removed?).
3. **Real-size reports:** table-aware parsing, BM25 plus embeddings, retrieval per field, a cache keyed on (document hash, form version).

## 9. Known limitations

- **Live model:** built first against the stand-in, as the build machine couldn't reach OpenAI. The first live `make eval` (gpt-4o-mini) scored 15/17 and found 2 bugs (AI_USAGE.md §3). After fixes, `REPEAT=2 make eval` scored 17/17 with matching verdicts, and all 12 factual answers passed the figure check (0 `figure_unsupported`). Two runs is a small sample.
- **Figure check runs after streaming:** it flags an unsupported number but can't take it off the screen. It matches numbers, not meaning, so a right number beside the wrong metric passes. The eval doesn't score `grounded` yet.
- **Default model** is `gpt-4o-mini`; set `OPENAI_MODEL` if your key lacks it.
- **Admin and user tokens** see the same data, so they're treated the same.
- **Small things:** heading detection assumes this generator's 13pt headings, metrics reset on restart, the log grows without limit.
- **Report vs company_api:** some CEO letters contradict company_api's FY2023 figures (WOLF, FERN, GRIP). Prefill uses the tables; a year-on-year question shows the conflict rather than resolving it.
