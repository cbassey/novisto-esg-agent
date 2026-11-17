"""Per-figure grounding for Q&A answers (app/grounding.py).

The pure checks run anywhere. The last test drives the real run_ask loop with a
scripted model and a stub company_api (no network, no OpenAI); it needs the app's
dependencies, so it is skipped where openai/pypdf are not installed.
Run: python -m pytest tests -q
"""

import asyncio
import json
import re
from types import SimpleNamespace

import pytest

from app.grounding import API_CITATION_RE, api_values, check_figures, figures_in

CITATION_RE = re.compile(r"([A-Z]{2,6}_report\.pdf)[\s,;:\-]{0,4}(?:p(?:age|g)?\.?)\s*(\d+)", re.IGNORECASE)
P3 = "Appendix: Data Tables\nScope 1 (tCO2e) 1,250\nScope 2 (tCO2e) 8,800\nScope 3 (tCO2e) 44,700"
PRIOR = {"fiscal_year": 2023, "scope_1_tco2e": 1314, "scope_2_tco2e": 9592}


def check(answer, api=None):
    return {f["value"]: f["status"] for f in check_figures(answer, CITATION_RE, {"ACME_report.pdf p.3": [P3]}, api or {})}


def test_prior_year_figure_under_report_citation_is_unsupported():
    # The real failure: 9,592 came from company_api but sat under the report citation.
    got = check("ACME's Scope 2 emissions decreased from 9,592 tCO2e in FY2023 to 8,800 tCO2e in FY2024, "
                "a reduction of 792 tCO2e [ACME_report.pdf p.3].")
    assert got == {"9,592": "unsupported", "8,800": "cited", "792": "unsupported"}


def test_api_figure_with_its_own_citation_is_cited_and_arithmetic_is_rechecked():
    got = check("Scope 2 fell from 9,592 tCO2e (prior year) [company_api ACME] to 8,800 tCO2e "
                "[ACME_report.pdf p.3], a reduction of 792 tCO2e (8.3%).",
                {"company_api ACME": api_values(PRIOR)})
    assert got == {"9,592": "cited", "8,800": "cited", "792": "derived", "8.3": "derived"}


def test_wrong_arithmetic_is_unsupported():
    got = check("Scope 2 fell from 9,592 [company_api ACME] to 8,800 [ACME_report.pdf p.3], down 800 tCO2e.",
                {"company_api ACME": api_values(PRIOR)})
    assert got["800"] == "unsupported"


def test_metric_names_years_units_and_pages_are_not_figures():
    assert figures_in("Scope 1, 2 and 3 for FY2024 in 2024 (tCO2e, m3) [ACME_report.pdf p.3].", CITATION_RE) == []
    assert [v for _, v in figures_in("It was 9% and 1,820 staff [company_api: ACME, FY2023].", CITATION_RE)] == [9, 1820]


def test_api_citation_needs_brackets_and_fiscal_year_is_not_support():
    assert API_CITATION_RE.findall("data from company_api for ACME") == []
    assert API_CITATION_RE.findall("[company_api ACME] (company_api: WOLF, FY2023)") == ["ACME", "WOLF"]
    assert 2023.0 not in api_values(PRIOR)


# --------------------------------------------------------------- run_ask, end to end

class ScriptedLLM:
    """Plays a model: search, fetch the prior year, then write `answer`."""
    model = "scripted"

    def __init__(self, answer):
        self.answer, self.step = answer, 0

    def cost_usd(self, *_):
        return 0.0

    async def stream(self, messages, tools=None, tool_choice=None):
        from app.llm import Completion
        self.step += 1
        plan = {1: ("search_documents", {"query": "Scope 2", "company": "ACME"}),
                2: ("fetch_prior_year", {"ticker": "ACME"})}
        if self.step in plan and tool_choice != "none":
            name, args = plan[self.step]
            yield "done", Completion(tool_calls=[{"id": f"c{self.step}", "name": name, "arguments": json.dumps(args)}])
            return
        for w in self.answer.split(" "):
            yield "delta", w + " "
        yield "done", Completion(content=self.answer)


class StubCompany:
    async def get_prior_year(self, principal, ticker):
        return {"ticker": ticker, "prior_year_disclosure": PRIOR}

    async def get_company(self, principal, ticker):
        return {"ticker": ticker, "name": "ACME Technologies", "fiscal_year": 2024, "headcount": 1820}


class StubIndex:
    def search(self, principal, query, companies, k=4):
        chunk = SimpleNamespace(chunk_id="ACME-3", doc="ACME_report.pdf", page=3, section="Appendix", text=P3)
        return SimpleNamespace(chunks=[chunk], withheld=[])


def run_ask(answer):
    pytest.importorskip("openai")
    pytest.importorskip("pypdf")
    from app.agent import Deps, run_ask as ask
    from app.auth import Principal, Secret

    principal = Principal("tenant_a", "Tenant A", ("ACME",), Secret("x"))
    deps = Deps(index=StubIndex(), company=StubCompany(), llm=ScriptedLLM(answer),
                company_names={"ACME": "ACME Technologies"}, form_spec={})

    async def collect():
        frames = []
        async for raw in ask(deps, principal, "How did ACME's Scope 2 change year on year?"):
            etype = re.search(r"^event: (.+)$", raw, re.M).group(1)
            frames.append((etype, json.loads(re.search(r"^data: (.+)$", raw, re.M).group(1))))
        return frames

    return asyncio.run(collect())


def test_run_ask_flags_the_overclaim_and_shows_returned_values():
    frames = run_ask("Scope 2 decreased from 9,592 tCO2e in FY2023 to 8,800 tCO2e in FY2024 [ACME_report.pdf p.3].")
    final = next(d for t, d in frames if t == "answer.final")
    assert final["grounded"] is False
    assert final["citations"] == [{"doc": "ACME_report.pdf", "page": 3}]  # shape the eval reads, unchanged
    assert [g["value"] for t, g in frames if t == "guardrail" and g["kind"] == "figure_unsupported"] == ["9,592"]
    prior = next(d for t, d in frames if t == "tool.call" and d["name"] == "fetch_prior_year")
    assert prior["result"]["values"]["scope_2_tco2e"] == 9592


def test_run_ask_accepts_the_api_citation():
    frames = run_ask("Scope 2 fell from 9,592 tCO2e [company_api ACME] to 8,800 tCO2e [ACME_report.pdf p.3].")
    final = next(d for t, d in frames if t == "answer.final")
    assert final["grounded"] is True
    assert final["api_citations"] == [{"source": "company_api", "ticker": "ACME"}]
    assert not [g for t, g in frames if t == "guardrail"]
