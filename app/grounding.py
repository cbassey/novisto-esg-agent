"""Per-figure provenance for Q&A answers.

Prefill checks every value against its cited passage (validate.py). Ask used to
check only that each citation pointed at a page retrieved in this request, so a
figure from company_api could sit under a report citation and still count as
grounded. This module checks every number in the answer against the sources
the answer itself cites:

  * [DOC p.N]           -> the number appears in that retrieved passage
  * [company_api TICKER] -> the number was returned by company_api for TICKER
                           in this request (fetch_company / fetch_prior_year)
  * derived             -> a difference or % change of two cited numbers,
                           recomputed here (the model's arithmetic is checked)

Anything else is `unsupported`. Pure module (no I/O, no app imports) so it is
unit-testable on its own. The text has already streamed, so the answer is
not rewritten: the figure is reported, a guardrail fires, and grounded=false.
"""

from __future__ import annotations

import re
from typing import Iterable


# "[company_api ACME]", "[company_api: ACME, FY2023]". Brackets required, so prose
# like "company_api for ACME" is not read as a citation of ticker FOR.
API_CITATION_RE = re.compile(r"[\[(]\s*company_api[\s:,\-]*([A-Z]{2,6})\b[^\])]{0,20}[\])]", re.IGNORECASE)

# Numbers inside a source passage (same parser Prefill's validate_field uses).
_NUM_RE = re.compile(r"(?<![\d.,])\d{1,3}(?:,\d{3})+(?:\.\d+)?(?![\d,])|(?<![\d.,])\d+(?:\.\d+)?(?![\d])")


def numbers_in(text: str) -> list[float]:
    return [float(m.replace(",", "")) for m in _NUM_RE.findall(text or "")]


# A figure in prose: not glued to letters/digits ("FY2023", "CO2", "p.3" are not figures).
_FIGURE_RE = re.compile(r"(?<![\w.,])(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)(?!\w|,\d|\.\d)")
# Metric names are not figures: "Scope 2", "Scope 1, 2 and 3".
_SCOPE_RE = re.compile(r"(?i)\bscopes?\s*\d(?:\s*(?:,|and|&|/|-|to|or)\s*\d)*\b")


def api_values(*records: dict | None) -> list[float]:
    """Every numeric value in company_api payloads (nested dicts included).
    fiscal_year is metadata, not a figure, so it never counts as support."""
    out: list[float] = []

    def walk(v):
        if isinstance(v, bool):
            return
        if isinstance(v, (int, float)):
            out.append(float(v))
        elif isinstance(v, dict):
            for k, x in v.items():
                if k != "fiscal_year":
                    walk(x)

    for r in records:
        walk(r or {})
    return out


def figures_in(answer: str, citation_re: re.Pattern) -> list[tuple[str, float]]:
    body = citation_re.sub(" ", answer or "")
    body = API_CITATION_RE.sub(" ", body)
    body = _SCOPE_RE.sub(" ", body)
    out = []
    for raw in _FIGURE_RE.findall(body):
        v = float(raw.replace(",", ""))
        if "," not in raw and "." not in raw and 1900 <= v <= 2100:
            continue  # a year, not a figure
        out.append((raw, v))
    return out


def _decimals(raw: str) -> int:
    return len(raw.split(".")[1]) if "." in raw else 0


def _derived_from(raw: str, v: float, pool: list[float]) -> bool:
    """v is |a-b|, or the % change between a and b, rounded as the answer wrote it.
    The pool is only figures the answer itself states AND that passed as cited, so
    arithmetic on an unsupported figure is unsupported too."""
    tol = 0.5 * 10 ** -_decimals(raw) + 1e-9
    for i, a in enumerate(pool):
        for b in pool[i + 1:]:
            if abs(abs(a - b) - v) <= tol:
                return True
            for base in (a, b):
                if base and abs(abs(a - b) / abs(base) * 100 - v) <= tol:
                    return True
    return False


def check_figures(answer: str, citation_re: re.Pattern,
                  cited_passages: dict[str, Iterable[str]],
                  cited_api: dict[str, list[float]]) -> list[dict]:
    """cited_passages: "DOC p.N" -> passage texts; cited_api: "company_api TICKER" -> values.
    Only sources the answer cites (and that were validated) are passed in.
    Returns one entry per figure, in answer order: {value, status, source}."""
    sources: list[tuple[str, list[float]]] = [
        (label, [n for t in texts for n in numbers_in(t)]) for label, texts in cited_passages.items()
    ] + list(cited_api.items())
    figs = figures_in(answer, citation_re)
    out = []
    for raw, v in figs:
        src = next((label for label, nums in sources if any(abs(n - v) < 1e-9 for n in nums)), None)
        out.append({"value": raw, "status": "cited" if src else "unsupported", "source": src})
    pool = sorted({v for (_, v), f in zip(figs, out) if f["status"] == "cited"})
    for (raw, v), f in zip(figs, out):
        if f["status"] == "unsupported" and _derived_from(raw, v, pool):
            f.update(status="derived", source="computed from cited figures")
    return out
