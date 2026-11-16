.PHONY: up down logs smoke demo eval eval-offline eval-adversarial help

help:
	@echo "Targets:"
	@echo "  make up     - bring up the scaffold (company_api + your app once wired)"
	@echo "  make down   - stop everything"
	@echo "  make logs   - tail all service logs"
	@echo "  make smoke  - sanity-check the company_api is reachable and auth works"
	@echo "  make demo   - exercise your /prefill and /ask endpoints end-to-end"
	@echo "  make eval   - run the evaluation harness (live OpenAI) -> eval/last_run.md"
	@echo "  make eval-offline     - same harness, scripted LLM stand-in (no key, no cost)"
	@echo "  make eval-adversarial - stand-in plays a compromised model; security cases MUST fail"

COMPOSE := docker compose
OFFLINE := docker compose -f docker-compose.yml -f docker-compose.offline.yml
AUTH_A := -H "Authorization: Bearer tok_tenant_a_user" -H "Content-Type: application/json"

up:
	@test -f .env || (echo "No .env found: copy .env.example to .env and add OPENAI_API_KEY"; exit 1)
	$(COMPOSE) up -d --build --wait --remove-orphans
	@curl -s http://localhost:8000/health; echo

down:
	$(OFFLINE) down --remove-orphans

logs:
	docker compose logs -f

# Sanity check: does company_api respond, and does bearer scoping work?
smoke:
	@echo "--- health ---"
	curl -sf http://localhost:8001/health | head
	@echo "\n--- tenant_a fetching ACME (allowed) ---"
	curl -sf -H "Authorization: Bearer tok_tenant_a_user" http://localhost:8001/companies/ACME | head
	@echo "\n--- tenant_a fetching GLOB (forbidden, expect 403) ---"
	-curl -s -o /dev/null -w "HTTP %{http_code}\n" -H "Authorization: Bearer tok_tenant_a_user" http://localhost:8001/companies/GLOB
	@echo "--- no bearer (expect 401) ---"
	-curl -s -o /dev/null -w "HTTP %{http_code}\n" http://localhost:8001/companies/ACME

# Streams raw SSE so you can see the event contract. Needs `make up`.
demo:
	@echo "=== Q&A (tenant A): streamed answer with citation ==="
	curl -sN $(AUTH_A) -X POST http://localhost:8000/v1/ask \
	  -d '{"question": "What was ACME'"'"'s Scope 2 emissions last fiscal year?"}'
	@echo "\n=== Prefill WOLF (tenant A): the report with the injected note ==="
	curl -sN $(AUTH_A) -X POST http://localhost:8000/v1/prefill -d '{"company": "WOLF"}'
	@echo "\n=== Cross-tenant prefill (tenant A -> INIT): refused before any model call ==="
	-curl -s -w "  HTTP %{http_code}\n" $(AUTH_A) -X POST http://localhost:8000/v1/prefill -d '{"company": "INIT"}'
	@echo "\n=== Proof the bearer is never logged ==="
	@grep '"auth.ok"' var/log/app.jsonl | tail -1
	@echo "occurrences of any bearer token in var/log/app.jsonl: $$(grep -c 'tok_tenant' var/log/app.jsonl || true)"

# Live run against OpenAI. Writes eval/last_run.md + eval/last_run.json.
# REPEAT=2 runs everything twice and reports whether verdicts are identical.
eval:
	$(COMPOSE) exec -T app python -m eval.run --repeat $${REPEAT:-1}

# Keyless: same harness, same service, scripted LLM stand-in. Restores the
# live stack afterwards. Output: eval/offline_run.*
eval-offline:
	FAKE_MODE=honest $(OFFLINE) up -d --build --wait
	-$(OFFLINE) exec -T app python -m eval.run --out eval/offline_run
	$(COMPOSE) up -d --build --wait --remove-orphans

# The stand-in tries the injected cross-tenant call. Expect security FAILs:
# this proves the scorer catches it. Output: eval/adversarial_run.*
eval-adversarial:
	FAKE_MODE=adversarial $(OFFLINE) up -d --build --wait --force-recreate fake_llm app
	-$(OFFLINE) exec -T app python -m eval.run --out eval/adversarial_run
	$(COMPOSE) up -d --build --wait --remove-orphans
