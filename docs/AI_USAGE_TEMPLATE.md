# AI_USAGE

**Rename to `AI_USAGE.md` and fill in.** We explicitly expect you to generate most of this code with AI. This document is the one thing AI can't ghost-write for you — it's how we see your judgment on top of the tools.

Be specific. "I used Cursor" is not an answer. "I used Claude Sonnet in Cursor to draft the SSE endpoint, it got the event framing wrong on the first pass (used `data:` without `event:`), I rewrote the generator to emit typed events" is.

---

## 1. Tools used

Which assistants, which models, roughly in what proportion. (Example: "Claude Sonnet 4.6 in Cursor for ~80 %, GPT-5 in ChatGPT for architecture sanity checks on the agent loop.")

## 2. Where AI shined

Two or three concrete moments. One-liners, not paragraphs.

- *"Generated the FastAPI + SSE skeleton in one prompt, worked first try."*
- *"Drafted the BM25 retriever including the tenant-scope filter — I just reviewed and kept it."*

## 3. Where AI got it wrong

Two or three concrete moments. **This is the signal.**

- *"First draft of the agent loop silently dropped tool-call errors — the model returned 'done' after a failed `fetch_company`. I caught it in eval, forced the agent to surface tool errors and re-plan."*
- *"AI-generated scorer graded the injection case as PASS when the stream was empty — fail-open bug. I rewrote it to fail-closed on missing fields."*

## 4. What you rewrote and why

The parts where you looked at AI output and said "no, this is wrong for production." Security code? Error handling? The agent loop? Why?

## 5. What you would never let AI ship unsupervised

A short list of things that must get human review in this codebase before merge. Be specific to this project, not generic ("tests", "docs" is not an answer).

## 6. Prompts or techniques worth sharing

One or two that materially helped. A system prompt pattern for the agent, a technique for getting structured output, a way of describing tools that improved selection accuracy. Code-review-shaped, not self-promotional.

## 7. What slowed you down

The thing that cost you the most time. Honest. We want to know what's genuinely hard, not what you wish you could tell us about.
