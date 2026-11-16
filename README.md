# Novisto — Senior AI Engineer Take-Home

## The scenario

Novisto's customers upload sustainability reports — long PDFs full of narrative, tables, and ESG disclosures. From that same corpus they need two things from us:

1. **Answer their questions in natural language.** *"What was ACME's Scope 2 emissions last year?"* *"Did they disclose a lost-time injury rate?"*
2. **Prefill their ESG disclosure forms automatically** — the structured forms they file with regulators and investors, pulled from the PDFs plus our internal company data.

You're building the small SaaS service that makes both work.

## What we hand you

- **10 synthetic sustainability reports** (PDFs) across different companies and sectors. One contains a buried prompt-injection string — real life.
- **A mock internal REST API** (`services/company_api/`) that returns structured company facts (sector, HQ, prior-year disclosures). Bearer-scoped, tenant-isolated.
- **The ESG form spec** you'll prefill (`data/form_spec.json`) — ~10 fields with units and a one-line glossary.
- **Ground truth to eval against** — 3 prefill cases + a handful of Q&amp;A pairs in `data/golden/`.
- **An OpenAI API key** (we send it with your invitation).
- **A docker-compose, a Makefile, a .env.example** — enough wiring that `make up` gives you a working company API in one command.

### Tenants & bearer tokens

Two tenants, four tokens. Companies are tenant-scoped. Any attempt to fetch a company outside the token's scope returns `403`. Any unknown or missing bearer returns `401`.

| Token                | Tenant             | Can see                      |
| -------------------- | ------------------ | ---------------------------- |
| `tok_tenant_a_user`  | Northstar Advisors | ACME, WOLF, GRIP, NOVA, FERN |
| `tok_tenant_a_admin` | Northstar Advisors | ACME, WOLF, GRIP, NOVA, FERN |
| `tok_tenant_b_user`  | Globex Partners    | GLOB, INIT, QUAD, RISE, TIDE |
| `tok_tenant_b_admin` | Globex Partners    | GLOB, INIT, QUAD, RISE, TIDE |

Full mapping also in `data/tenants.json`. The injected PDF (one of the ten) lives inside `tenant_a`'s scope and tries to coerce the agent into fetching a `tenant_b` company — your tool permissions must hold.

## What you build

A service that:

- **Ingests** the 10 PDFs into a retrieval system you design . Must enforce tenant scope inside retrieval (no cross-tenant chunks leak).
- **Exposes an agent through an HTTP API with two capabilities** : Q&amp;A over the corpus, and form prefill from the corpus + the company REST API. **You design the API shape** : endpoint names, request/response contracts, streaming event format. Document it in `DESIGN.md`.
- **Streams responses** so the caller sees progress, not just a final blob.
- **Enforces security** — bearer token in, scoped to a tenant, every tool call rechecks scope, nothing leaks cross-tenant, no bearer in logs or in the stream.
- **Implements observability** — you decide what matters to instrument and how. No bearer in logs.
- **Proves it works** — you design the evaluation. Pick your OSS framework and run it against the ground truth we ship. See `eval_spec.md` for what must be covered.
- **Ships a tiny UI** so you and we can run it end-to-end. Not evaluated. AI-generate it.

Everything runs locally. `make up` brings it all up. `make demo` exercises both capabilities. `make eval` runs your harness.

## The bar

**2 hours of focused, AI-assisted work.** 

This is a SaaS service, not a notebook , but we are **not** asking for production-grade polish. We want to see the **concepts** that go to production, demonstrated honestly:

- A bearer actually propagated through to every tool (not a TODO)
- One log line proving it isn't leaked
- One eval case catching the injected PDF's attempt at cross-tenant exfil
- One `docker compose up` that really works first try

## What we deliberately leave to you


|                            |                                                                                                    |
| -------------------------- | -------------------------------------------------------------------------------------------------- |
| **Project layout**         | How you split agent / tools / retrieval / API / auth is yours. We don't ship an `app/` scaffold.   |
| **API shape**              | Endpoint names, request/response contracts, streaming format — your call, documented in DESIGN.md. |
| **Retrieval architecture** | Chunking, embeddings, ranker, storage — your call, defended in DESIGN.md.                            |
| **Evaluation**             | Framework (Phoenix, Langfuse, Ragas, custom), scorer, output format — your call. See `eval_spec.md` for what must be covered. |
| **Framework**              | LangGraph vs plain OpenAI SDK vs custom. FastAPI expected but not mandated.                        |


## Stack rules

- **OSS only, runs locally** on macOS Apple Silicon (arm64), 16 GB RAM, Docker Desktop
- **OpenAI API is the one SaaS exception** — the key we send you goes in `.env`, nowhere else
- **Python 3.11+** — the agent, tools, API, retrieval, and eval must all be Python code we can read
- **No no-code / low-code platforms** (n8n, Dify, Flowise, Langflow, etc.). Python libraries like LangGraph, LangChain, LlamaIndex are fine — drag-and-drop workflow builders are not.

## Out of scope

- Real user auth (JWT signing, OAuth) — a shared-secret bearer in `data/tenants.json` is the full auth model
- Cloud deployment
- Fine-tuning or model training
- A production vector store
- High test coverage
- UI polish

## First 10 minutes

If `make up` doesn't give you a working response from the company API within 10 minutes, something is wrong on our side — **reach out**. Don't waste time debugging our scaffold.

## Deliverables

1. **A git repository** (public, private, or zip) with this scaffold plus everything you built.
2. **Your code**
3. **`docs/DESIGN.md`** (≤ 2 pages, from the template) — architecture, API shape you chose, retrieval design, auth model, cost strategy, eval design rationale, what you'd do with another day.
4. **`docs/AI_USAGE.md`** (from the template) — how you used AI, what it got wrong, what you rewrote, what you'd never let AI ship unsupervised.
5. **Your eval output** (`eval/last_run.*` — text, HTML, screenshot + JSON dump, whatever your tool produces) — a captured run showing it passing.

## What happens next

We run your repo on our laptop, then hold a **45 60 min live session** where we walk through it. 
---

## Running this submission

```bash
cp .env.example .env          # paste OPENAI_API_KEY (optionally OPENAI_MODEL)
make up                       # company_api + app, waits until both are healthy
make demo                     # streams /v1/ask and /v1/prefill, shows a 403, proves no bearer in logs
make eval                     # live run -> eval/last_run.md + eval/last_run.json   (REPEAT=2 for a determinism check)
open http://localhost:8000    # tiny UI
```

No key? `make eval-offline` runs the same harness against a scripted LLM stand-in (plumbing and security only), and `make eval-adversarial` shows the scorer failing when the "model" attempts the injected cross-tenant call. Unit checks for the scorer traps: `pip install -r app/requirements.txt pytest && python -m pytest tests -q`.

Design: [`docs/DESIGN.md`](docs/DESIGN.md) · AI usage: [`docs/AI_USAGE.md`](docs/AI_USAGE.md)
