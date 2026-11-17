# AI_USAGE

## 1. Tools used

Claude (Opus) in Claude Code, running as an agent with shell and Docker access. It wrote pretty much all the code, the eval and the first drafts of the docs. I steered, reviewed, ran things and pushed back.

Before I'd call any of it mine, I had it draw me a diagram of the whole flow with a 1–5 "how technical" slider. I went through it level by level until I could explain every step myself. That's also how I found the biggest bug (see section 3).

## 2. Where AI shined

- **It read the data before writing code.** It checked every number in the 10 PDFs against the golden files and the company API. That's how we found that the API's "last year" figures are just this year's numbers times a factor, so they're only 4–9% off. A "close enough" scorer would pass stale data. It also found that the shipped Q&A checks would count "9%" as 9 incidents, and "tenant" as containing "ten".
- **It built a fake OpenAI.** The build machine couldn't reach OpenAI, so it made a scripted stand-in that talks the same API. The real code path ran end to end without spending tokens. It also had a "bad model" mode, to prove the eval actually fails when it should.
- **Some things just worked first time:** the per-tenant search index and the streaming.

## 3. Where AI got it wrong

- **It said an answer was "grounded" when it wasn't.** I caught this one. I asked how ACME's Scope 2 changed year on year. It said 9,592 → 8,800, down 792, all cited to one report page, and marked it grounded. Only 8,800 is on that page. 9,592 came from the company API, and 792 was the model doing its own math. The check only looked at whether the *page* was real, never whether the *numbers* were on it. Now every number has to be on the page it cites, or come from the API with its own `[company_api ACME]` tag, or be math we redo ourselves. If not, the answer gets flagged as not grounded.
- **The name filter leaked in pieces.** The filter that hides other tenants' names worked on whole words. Real OpenAI streams tiny chunks like "Glob" + "ex", so the name slipped through in two halves. The fake model sent whole words, so we only saw this on the first live run. The eval caught it, because it checks that the streamed pieces add up to the final answer.
- **A right answer with no source.** Asked about Globex's board size, the model answered "10" straight from the company API, never searched the report, and so cited nothing. Telling it "always cite the report" in the prompt didn't work. It did the same thing again. So the code now forces a search if the model looks up a company without searching.
- **The UI showed the wrong tenant's data.** I caught this one too. I switched from tenant A's token to tenant B's, and tenant A's form was still on screen. Nothing actually leaked from the server, but it looked like it had. Now switching tokens clears the screen.
- **Smaller stuff:**
  - The name filter would have blanked the normal words "rise" and "tide", because RISE and TIDE are tickers.
  - The eval would have failed an honest "I can't help with that" if the user's own question named another tenant's company.
  - Search ranked cover pages above the data tables.
  - The citation format was too strict: real models write "page 3", not just "p.3".

## 4. What I rewrote and why

- **What "grounded" means.** A citation existing isn't proof. Every number has to trace back to the exact source next to it. Someone is going to copy that number into a regulator filing.
- **A blocked cross-tenant attempt still fails the eval.** The system stopped it, but it means a document managed to steer the model. Passing it would hide that.
- **Blank beats wrong in the form.** If a value can't be found on the page it cites, the field is left empty. Someone will review an empty field. A wrong one just gets filed.
- **Rules go in code, not just the prompt.** The "always search first" prompt rule got ignored live, so it's enforced in the loop now.

## 5. What I'd never let AI ship unsupervised (in this codebase)

- **The scope checks** (`tools.py`) **and the per-tenant search index** (`retrieval.py`). One flipped condition and tenant A sees tenant B's data. The tests can still pass if the same AI wrote them.
- **The log redaction** (`logs.py`). One new log line that dumps the request headers and the token is in the logs.
- **The injection patterns** (`guards.py`). It's easy to make them catch the one planted example and miss a reworded one.
- **The grounding checks** (`validate.py`, `grounding.py`). Loosening a tolerance "to fix a flaky test" turns them into a rubber stamp.
- **The eval scorers.** If AI writes both the code and the grader, they'll agree with each other. They need testing against bad answers written by hand, and against a model that misbehaves on purpose (`make eval-adversarial`).

## 6. Prompts or techniques worth sharing

- **Don't explain the rules to the model. Take the options away.** For prefill, the model can only pick one company (the one you asked for), and it doesn't get the company lookup tool at all. The injected text has nothing to work with.
- **Make the model show its sources in a form code can check.** Page, quote and value for every field, and a tag next to every number. Then code decides if it holds up, not the model.
- **Make it explain the system back to you, simple first, then deeper.** That's how I found the grounding bug.

## 7. What slowed me down

- **No live model until late.** The build machine couldn't reach OpenAI or Docker Hub, so early testing used the fake model. The two worst bugs only showed up on the first real run.
- **Actually understanding it.** AI writes a lot of code fast. The slow part was getting to where I could explain every piece myself, and that's where the real bugs turned up.

## What I learned

- A green test suite means nothing if the same AI wrote the code and the tests. The bugs that mattered showed up on a real run or when I poked at it myself.
- "It has a citation" and "it's correct" are different things. Check the numbers, not just the links.
- If a rule really matters, put it in code. The model will skip prompt rules.
- Make the AI explain its work back to you. That's the fastest way to find what it got wrong.
