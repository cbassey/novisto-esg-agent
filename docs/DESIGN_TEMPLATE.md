# DESIGN

**≤ 2 pages. Rename to `DESIGN.md` and fill in.** We read this before we run the code. Be opinionated and concise. Named rejections beat vague mentions — *"I picked LangGraph over a hand-rolled state machine because X"* beats *"I considered a few frameworks"*.

---

## 1. Architecture at a glance

One diagram or ~5 lines of ASCII. How requests flow, where state lives, where retrieval sits, how the stream gets back to the client.

## 2. API shape you chose

You designed the endpoints. Document them.

- The endpoints you expose (names, methods, request/response shapes)
- The streaming event format (what events, what payloads)
- Why this shape for Q&A vs. prefill — one shared endpoint, two endpoints, or something else

## 3. Framework & stack choices

| Area | Picked | Rejected | Why |
|------|--------|----------|-----|
| Orchestration | e.g. LangGraph | plain SDK, custom FSM | ... |
| Retrieval | e.g. BM25 + embeddings hybrid | pure BM25, pgvector, Qdrant | ... |
| API | FastAPI | ... | ... |
| Observability | stdlib `logging` + JSON formatter | structlog, OTel | ... |

## 4. Retrieval design

- Chunking strategy and size — why
- Embedding model (or BM25) — why
- Ranker — why
- **How you enforce tenant scope inside retrieval** — this is security code, call it out

## 5. Auth & tenant model

- How the bearer flows from the inbound request → the agent → each tool
- Where tenant scope is enforced (in which layer, with what check)
- What happens when a tool is called with an out-of-scope arg
- How you prevent the bearer from being logged or streamed

## 6. Observability

- What you chose to instrument (cost, latency, tokens, tool-call timeline, errors — your pick) and why
- Where it lives (stdout JSON, a /metrics endpoint, a file, something else)
- The one metric you'd want oncall to page on — and the threshold

## 7. Evaluation approach

- **Framework/tooling you picked** (Phoenix, Langfuse, Ragas, DeepEval, pytest, custom script, something else) and why. What you rejected and why.
- How you defined "correct" for each kind of case (numeric fields, Q&A, security)
- What cases you added beyond the required (3 prefill + Q&A + injection)
- How your scorer handles edge cases (missing fields, unit mismatches, Q&A phrasing variance)
- What you'd measure if you had another hour

## 8. What you'd do with another day

Three things, in priority order. Not "add more tests." Something real — a retry policy, a cache, a specific guardrail, a schema migration for the form, whatever reveals what you'd harden first.

## 9. Known limitations

What's scrappy on purpose. What would need real work before production. We respect honest limitations more than hidden ones.
