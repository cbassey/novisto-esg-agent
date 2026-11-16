# AI_USAGE

> **DRAFT — rewrite in your own voice before submitting.** The facts below are what actually happened during the build session. Sections 5 and 6 need *your* judgment; the bullets there are starting points, not answers.

## 1. Tools used

- Claude (Opus, agentic session with shell and Docker access) for ~all code, the eval harness, and the first draft of DESIGN.md.
- `<add anything else you used, e.g. your IDE assistant, for review passes>`

## 2. Where AI shined

- Discovery before code: it cross-checked every field of all 10 PDFs against the golden files and the company API. That surfaced that the API's FY2023 figures are the FY2024 values times fixed factors (a decoy for any "close enough" scorer), and that the shipped Q&A matchers accept "9%" for 9 incidents and "tenant" for "ten".
- Built a scripted OpenAI-compatible stand-in (`tests/fake_llm`) when the build machine couldn't reach OpenAI. This exercised the *real* SDK, streaming and tool loop end to end, including a "compromised model" mode, without spending tokens.
- The tenant-partitioned BM25 index and the SSE framing were right first time.

## 3. Where AI got it wrong

- **Stream filter leaked a name in two halves (caught by the first live run):** the scrubber that withholds other tenants' names from the token stream fell back to cutting mid-word when its buffer had no space to split at. Real OpenAI streams tiny tokens ("Glob", "ex"), so "Globe" + "x" reached the client unfiltered while the final answer was filtered correctly. The offline stand-in streamed whole words and never triggered it. The eval's "deltas must reassemble into the final answer" check flagged it. Fixed by scrubbing the whole buffer before any cut and never cutting inside a word; regression test uses the exact live token sequence.
- **Uncited correct answer (live run):** asked about Globex's board, the model answered "10" from `fetch_company` without searching the report, so there was no citation and the case failed. The scorer was right: the product promise is a traceable source. A prompt rule ("every figure must be backed by a report passage") was not enough: the second live run gave the same uncited answer. Fixed in the loop: once the model has called `fetch_company` without searching, the next step forces `search_documents`. Out-of-scope questions never reach `fetch_company`, so refusals are unchanged.
- **Egress guard false positives (caught before running):** the first version withheld company brand words case-insensitively, which would have redacted the English words "rise" and "tide" from answers (RISE, TIDE are tickers). Fixed: brands equal to their ticker are matched case-sensitively as tickers.
- **Retrieval ranking:** "What percentage of RISE Healthcare's senior leadership are women?" returned the cover and About sections above the workforce table, because the company name dominated BM25. Caught by the eval; fixed by stripping the company's own name from company-filtered queries.
- **Eval self-sabotage:** the first security scorer would have failed a clean refusal whenever the *user's own question* named another tenant's company and the model echoed it into a search query. Fixed: words the user typed are exempt from the mention check, while forbidden numbers and tool calls never are.
- **Citation parser too strict:** it only accepted `[DOC p.N]`. Real models write "page 3", "p. 3", or several citations in one bracket. Loosened the format; substance (cited page must be in this request's evidence) stays strict.
- **Docker:** the app's CMD relied on a `uvicorn` console script being on PATH; switched to `python -m uvicorn`.

## 4. What you rewrote and why

`<Your call. Candidates worth owning: the scope re-check in tools.execute (security code), validate.py's "missing beats wrong" policy, and the decision that a contained cross-tenant attempt still FAILS the eval.>`

## 5. What you would never let AI ship unsupervised (in this codebase)

`<Write your own. Starting points:>`
- `tools.py` scope checks and `retrieval.py` tenant partitioning: one inverted condition is a cross-tenant leak.
- `logs.py` redaction: a new log call with a header dict would bypass intent if the filter regressed.
- `guards.py` injection patterns: easy to make them "pass the test" and useless against a paraphrase.
- The eval scorers themselves: an AI that writes both the system and its grader will make them agree. Review the scorer against hand-written adversarial answers (see `tests/test_scoring_and_guards.py`).

## 6. Prompts or techniques worth sharing

- **Shrink the tool schema to the request's scope** instead of describing the policy in the prompt. The prefill model sees a `company` enum with one value and has no `fetch_company` tool at all. The injection has nothing to grab.
- **Make provenance machine-checkable:** the form schema forces `source/doc/page/quote` per field, and `validate.py` nulls any value not found in the cited passage. This turns "the model said so" into a verifiable claim.
- **Test the grader with a model that misbehaves on purpose** (`make eval-adversarial`): a scorer that has never been shown failing is not evidence.

## 7. What slowed you down

`<Yours. Facts from the session: no OpenAI or Docker Hub access from the build machine meant building a stand-in model and a stand-in base image to test the compose path; the live model run happens for the first time on your laptop.>`
