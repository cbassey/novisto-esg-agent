"""Tenant-partitioned BM25 retrieval.

SECURITY CODE. Each tenant gets its own physical index, built only from that
tenant's chunks. A query can only reach `indexes[principal.tenant_id]`, so a
cross-tenant chunk is not filtered out after the fact; it is never a
candidate. A second check asserts tenant ownership on every result anyway.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass

from .auth import Principal
from .ingest import Chunk

_TOKEN_RE = re.compile(r"[a-z0-9]+(?:[.,][0-9]+)*")
_STOP = set("the a an of in on for to and or is was were our we what how many much did does by at as with from this that".split())


def tokenize(text: str) -> list[str]:
    toks = []
    for t in _TOKEN_RE.findall(text.lower()):
        t = t.replace(",", "")
        if t not in _STOP:
            toks.append(t)
    return toks


class BM25:
    def __init__(self, chunks: list[Chunk], k1: float = 1.4, b: float = 0.6):
        self.chunks = chunks
        self.k1, self.b = k1, b
        self.docs = [tokenize(f"{c.section} {c.text}") for c in chunks]
        self.avgdl = sum(map(len, self.docs)) / max(len(self.docs), 1)
        df = Counter(t for d in self.docs for t in set(d))
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
        self.tf = [Counter(d) for d in self.docs]

    def scores(self, query: str) -> list[float]:
        q = tokenize(query)
        out = []
        for tf, d in zip(self.tf, self.docs):
            s = 0.0
            for t in q:
                if t in tf:
                    f = tf[t]
                    s += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * len(d) / self.avgdl))
            out.append(s)
        return out


@dataclass
class SearchResult:
    chunks: list[Chunk]
    withheld: list[Chunk]  # quarantined chunks that matched but were not returned


class TenantIndex:
    def __init__(self, chunks: list[Chunk]):
        by_tenant: dict[str, list[Chunk]] = defaultdict(list)
        for c in chunks:
            by_tenant[c.tenant_id].append(c)
        self._indexes = {t: BM25(cs) for t, cs in by_tenant.items()}

    def stats(self) -> dict[str, int]:
        return {t: len(ix.chunks) for t, ix in self._indexes.items()}

    def search(self, principal: Principal, query: str, companies: tuple[str, ...], k: int = 4) -> SearchResult:
        """`companies` must already be a subset of principal.companies (tools.py checks)."""
        ix = self._indexes.get(principal.tenant_id)
        if ix is None:
            return SearchResult([], [])
        allowed = set(companies) & set(principal.companies)
        ranked = sorted(
            (
                (s, i)
                for i, s in enumerate(ix.scores(query))
                if ix.chunks[i].ticker in allowed and s > 0
            ),
            key=lambda x: (-x[0], ix.chunks[x[1]].chunk_id),  # deterministic tie-break
        )
        hits, withheld = [], []
        for _, i in ranked:
            c = ix.chunks[i]
            if c.tenant_id != principal.tenant_id:  # belt and braces
                raise RuntimeError("cross-tenant chunk reached ranking")
            (withheld if c.quarantined else hits).append(c)
            if len(hits) >= k:
                break
        return SearchResult(hits, withheld)

    def company_chunks(self, principal: Principal, ticker: str) -> SearchResult:
        """All sections of one company's report, in page order (for prefill)."""
        ix = self._indexes.get(principal.tenant_id)
        if ix is None or not principal.can_access(ticker):
            return SearchResult([], [])
        cs = [c for c in ix.chunks if c.ticker == ticker and c.tenant_id == principal.tenant_id]
        return SearchResult([c for c in cs if not c.quarantined], [c for c in cs if c.quarantined])

    def get(self, principal: Principal, chunk_id: str) -> Chunk | None:
        ix = self._indexes.get(principal.tenant_id)
        if ix is None:
            return None
        for c in ix.chunks:
            if c.chunk_id == chunk_id:
                return c
        return None
