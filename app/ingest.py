"""PDF -> section chunks with provenance.

Chunk boundary = report section (detected as >=13pt text, with a plain-text
fallback). Sections are small (50-150 words), self-contained, and keep table
rows intact, which is what both Q&A citations and prefill grounding need.

Tenant ownership is derived from the filename ticker joined to companies.json.
SECURITY: a PDF whose ticker has no known owner is NOT indexed (fail closed).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import pypdf

from .guards import instruction_like
from .logs import event

FILENAME_RE = re.compile(r"^([A-Z]{2,6})_report\.pdf$")
HEADING_MIN_PT = 13.0


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    doc: str
    ticker: str
    tenant_id: str
    page: int
    section: str
    text: str
    flags: tuple[str, ...] = field(default=())

    @property
    def quarantined(self) -> bool:
        return "instruction_like" in self.flags


def _headings(page: pypdf.PageObject) -> list[str]:
    found: list[str] = []

    def visit(text, cm, tm, font_dict, font_size):
        size = abs(font_size * (tm[0] or 1.0))
        if text.strip() and size >= HEADING_MIN_PT:
            found.append(text.strip())

    page.extract_text(visitor_text=visit)
    return found


def _clean_lines(raw: str, running_header: str | None) -> list[str]:
    out = []
    for line in raw.splitlines():
        s = " ".join(line.split())
        if not s or re.fullmatch(r"Page \d+", s) or (running_header and s == running_header):
            continue
        out.append(s)
    return out


def chunk_pdf(path: Path, ticker: str, tenant_id: str) -> list[Chunk]:
    reader = pypdf.PdfReader(str(path))
    chunks: list[Chunk] = []
    running_header = None
    for pno, page in enumerate(reader.pages, start=1):
        raw = page.extract_text() or ""
        lines = _clean_lines(raw, running_header)
        if pno == 1:
            # Cover page: title + sector + HQ. Learn the running header.
            title = lines[0] if lines else ticker
            running_header = f"{title} - Sustainability Report 2024" if lines else None
            chunks.append(_mk(path, ticker, tenant_id, pno, "Cover", lines))
            continue
        lines = [ln for ln in lines if not ln.endswith("- Sustainability Report 2024")]
        heads = set(_headings(page))
        section, buf = "Untitled", []
        for ln in lines:
            if ln in heads:
                if buf:
                    chunks.append(_mk(path, ticker, tenant_id, pno, section, buf))
                section, buf = ln, []
            else:
                buf.append(ln)
        if buf:
            chunks.append(_mk(path, ticker, tenant_id, pno, section, buf))
    return chunks


def _mk(path: Path, ticker: str, tenant_id: str, page: int, section: str, lines: list[str]) -> Chunk:
    text = "\n".join(lines)
    flags = ("instruction_like",) if instruction_like(text) else ()
    slug = re.sub(r"[^a-z0-9]+", "-", section.lower()).strip("-")
    return Chunk(
        chunk_id=f"{path.name}#p{page}-{slug}",
        doc=path.name,
        ticker=ticker,
        tenant_id=tenant_id,
        page=page,
        section=section,
        text=text,
        flags=flags,
    )


def ingest_corpus(data_dir: Path) -> list[Chunk]:
    companies = json.loads((data_dir / "companies.json").read_text())
    chunks: list[Chunk] = []
    for pdf in sorted((data_dir / "pdfs").glob("*.pdf")):
        m = FILENAME_RE.match(pdf.name)
        owner = companies.get(m.group(1), {}).get("tenant_id") if m else None
        if not owner:
            event("ingest.skipped_unowned_document", doc=pdf.name)
            continue
        doc_chunks = chunk_pdf(pdf, m.group(1), owner)
        for c in doc_chunks:
            if c.quarantined:
                # Note: never log chunk text; it may carry the injected payload.
                event("ingest.chunk_quarantined", doc=c.doc, page=c.page, section=c.section,
                      reason="instruction_like")
        chunks.extend(doc_chunks)
        event("ingest.document", doc=pdf.name, tenant_id=owner, chunks=len(doc_chunks))
    return chunks
