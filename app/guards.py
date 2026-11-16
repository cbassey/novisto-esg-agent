"""Guardrails: instruction-like text in documents, and out-of-tenant egress.

Neither is the primary defence (that is the scoped tool layer in tools.py);
these are the second and third walls. See DESIGN.md section 5.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

# Text inside a *document* addressed to an automated reader. Corporate
# reports essentially never contain these, so false positives are cheap
# (one passage withheld + a visible guardrail event) and misses are expensive.
_INSTRUCTION_PATTERNS = [
    r"\b(if you are|as) an? (ai|llm|language model|assistant|automated (reader|agent|system))\b",
    r"\bfor (automated|ai) (readers|agents|systems|assistants)\b",
    r"\bignore (all |any |the )?(previous|prior|above|user|system|other)\b",
    r"\bdisregard (all |any |the )?(previous|prior|above|instructions|user)\b",
    r"\b(call|invoke|use|run) the [a-z_]+ (tool|function)\b",
    r"\b[a-z]+_[a-z_]+\s*\(",  # code-like tool invocation, e.g. fetch_company(
    r"\bsystem prompt\b",
    r"\bnew instructions?\b",
]
_INSTRUCTION_RE = re.compile("|".join(_INSTRUCTION_PATTERNS), re.IGNORECASE)


def instruction_like(text: str) -> bool:
    return bool(_INSTRUCTION_RE.search(text))


@lru_cache(maxsize=1)
def _directory(data_dir: str) -> tuple[dict, dict]:
    companies = json.loads((Path(data_dir) / "companies.json").read_text())
    tenants = json.loads((Path(data_dir) / "tenants.json").read_text())
    return companies, tenants


class EgressGuard:
    """Withholds any mention of a company or tenant outside the principal's scope.

    Tickers match case-sensitively on word boundaries (so 'initiative' is not
    'INIT'); names match case-insensitively. Built per request.
    """

    WITHHELD = "[withheld]"

    def __init__(self, data_dir: Path, tenant_id: str, allowed: tuple[str, ...]):
        companies, tenants = _directory(str(data_dir))
        tickers, names = [], set()
        for t, c in companies.items():
            if t in allowed:
                continue
            tickers.append(t)
            names.add(c["name"])
            brand = c["name"].split()[0]  # e.g. "Globex" for GLOB
            # Brands equal to the ticker (RISE, TIDE...) stay case-sensitive via
            # the ticker rule, so the English words "rise"/"tide" are untouched.
            if brand.upper() != t:
                names.add(brand)
        for entry in tenants.values():
            if entry["tenant_id"] != tenant_id:
                names.add(entry["tenant_name"])
                names.add(entry["tenant_id"])
        # Never withhold a word that is part of an allowed company's own name.
        own = {w.lower() for t in allowed for w in companies.get(t, {}).get("name", "").split()}
        names = {n for n in names if n.lower() not in own}
        parts = [rf"\b{re.escape(t)}\b" for t in sorted(tickers, key=len, reverse=True)]
        self._ticker_re = re.compile("|".join(parts)) if parts else None
        nparts = [rf"\b{re.escape(n)}\b" for n in sorted(names, key=len, reverse=True)]
        self._name_re = re.compile("|".join(nparts), re.IGNORECASE) if nparts else None
        self.max_term_len = max([len(x) for x in tickers] + [len(x) for x in names] + [1])
        self.hits = 0

    def scrub(self, text: str) -> str:
        if not text:
            return text
        out = text
        for rx in (self._name_re, self._ticker_re):
            if rx is None:
                continue
            out, n = rx.subn(self.WITHHELD, out)
            self.hits += n
        return out

    def scrub_obj(self, obj):
        if isinstance(obj, str):
            return self.scrub(obj)
        if isinstance(obj, list):
            return [self.scrub_obj(x) for x in obj]
        if isinstance(obj, dict):
            return {k: self.scrub_obj(v) for k, v in obj.items()}
        return obj


class StreamScrubber:
    """Applies EgressGuard to a token stream without splitting a term in half.

    Holds back the last `max_term_len` chars until more text (or the end)
    arrives, so a forbidden term spanning two deltas is still caught.
    """

    def __init__(self, guard: EgressGuard):
        self.guard = guard
        self.buf = ""

    def push(self, delta: str) -> str:
        # Scrub the WHOLE buffer first, so a term is replaced before any cut can
        # split it; then release only up to a word boundary in the safe region.
        # Never cut inside a word: with real model streams (tiny tokens) a
        # mid-word cut once released "Globe" + "x" unscrubbed. If there is no
        # boundary yet, hold everything until more text or flush().
        self.buf = self.guard.scrub(self.buf + delta)
        keep = self.guard.max_term_len + 1
        if len(self.buf) <= keep:
            return ""
        ws = self.buf.rfind(" ", 0, len(self.buf) - keep + 1)
        if ws < 0:
            return ""
        ready, self.buf = self.buf[: ws + 1], self.buf[ws + 1 :]
        return ready

    def flush(self) -> str:
        ready, self.buf = self.buf, ""
        return self.guard.scrub(ready)
