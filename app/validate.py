"""Grounding validation for prefilled fields.

A proposed value is accepted only if it can be traced to evidence fetched in
THIS request, for THIS company:
  * source=report      -> the number (or string) appears in the cited passage
                          of the company's own report.
  * source=company_api -> it equals the value company_api returned to us.
Anything else is rejected (value nulled, proposal kept for the reviewer).
A missing value is better than a wrong one on a regulatory form.
"""

from __future__ import annotations


from .grounding import numbers_in  # noqa: F401  (re-exported; one number parser for both checks)
from .tools import ToolContext

API_BACKED = {"sector": "sector", "headcount": "headcount", "board_size": "board_size"}


def _norm(s: str) -> str:
    return " ".join(str(s).lower().split())


def _coerce(ftype: str, v):
    if v is None:
        return None
    if ftype in ("number", "integer"):
        try:
            f = float(str(v).replace(",", "").replace("%", "").strip())
        except ValueError:
            return None
        if ftype == "integer":
            return int(f) if f.is_integer() else None
        return f
    return str(v).strip() or None


def _same(ftype: str, a, b) -> bool:
    if a is None or b is None:
        return False
    if ftype in ("number", "integer"):
        return abs(float(a) - float(b)) < 1e-9
    return _norm(a) == _norm(b)


def validate_field(spec: dict, proposal: dict, ctx: ToolContext, ticker: str, prior: dict | None) -> dict:
    name, ftype = spec["name"], spec.get("type", "string")
    value = _coerce(ftype, (proposal or {}).get("value"))
    source = (proposal or {}).get("source") or "not_found"
    doc, page = (proposal or {}).get("doc"), (proposal or {}).get("page")
    out = {"name": name, "value": None, "unit": spec.get("unit"), "status": "missing", "source": source,
           "doc": None, "page": None, "flags": []}

    if value is None or source == "not_found":
        out["source"] = "not_found"
        return out

    verified = False
    if source == "report":
        expected_doc = f"{ticker}_report.pdf"
        passages = [c for c in ctx.seen_chunks.values() if c.doc == expected_doc and c.page == page]
        if doc != expected_doc:
            out["flags"].append("cited_wrong_document")
        elif not passages:
            out["flags"].append("cited_page_not_in_evidence")
        elif ftype in ("number", "integer"):
            verified = any(_same(ftype, value, n) for c in passages for n in numbers_in(c.text))
            if not verified:
                out["flags"].append("value_not_in_cited_passage")
        else:
            verified = any(_norm(value) in _norm(c.text) for c in passages)
            if not verified:
                out["flags"].append("value_not_in_cited_passage")
    elif source == "company_api":
        facts = ctx.api_facts.get(ticker, {})
        key = API_BACKED.get(name)
        verified = key is not None and _same(ftype, value, facts.get(key))
        if not verified:
            out["flags"].append("value_not_returned_by_company_api")
    else:
        out["flags"].append("unknown_source")

    if not verified:
        out.update(status="rejected", rejected_value=value)
        return out

    out.update(value=value, status="verified", doc=doc if source == "report" else None,
               page=page if source == "report" else None)

    # Cross-source check: report vs system of record.
    key = API_BACKED.get(name)
    api_val = ctx.api_facts.get(ticker, {}).get(key) if key else None
    if source == "report" and api_val is not None and not _same(ftype, value, api_val):
        out["status"] = "conflict"
        out["flags"].append(f"company_api_says:{api_val}")

    # Sanity vs prior year (flag only; never used as a value).
    pv = (prior or {}).get(name)
    if ftype in ("number", "integer") and pv not in (None, 0) and value is not None:
        change = (float(value) - float(pv)) / abs(float(pv))
        out["yoy_change_pct"] = round(change * 100, 1)
        if abs(change) > 0.5 and abs(float(value) - float(pv)) >= 5:
            out["flags"].append("large_yoy_change")
        if _same(ftype, value, pv):
            out["flags"].append("equals_prior_year_value")
    return out
