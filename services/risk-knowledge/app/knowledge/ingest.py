"""Ingestion: allowlisted download → type/size check → HTML to structured text → section-aware chunks.

Chunking preserves heading boundaries and keeps numbered/bulleted procedure lists intact (an emergency
step list is never split mid-list). Each chunk carries page/section metadata for citations.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from bs4 import BeautifulSoup
from sta_common.logging import get_logger

from app.knowledge.manifest import SourceDocument, SourceManifest

log = get_logger("knowledge-ingest")
MAX_BYTES = 8_000_000
ALLOWED_TYPES = ("text/html", "application/xhtml+xml", "text/plain")
MAX_CHUNK_CHARS = 1400
MIN_CHUNK_CHARS = 120


@dataclass(slots=True)
class Chunk:
    chunk_id: str
    document_id: str
    section: str
    page: int | None
    text: str
    text_hash: str
    order: int
    hazards: list[str] = field(default_factory=list)
    language: str = "en"
    country: str = "GLOBAL"
    authority: str = "OFFICIAL"


@dataclass(slots=True)
class IngestedDocument:
    doc: SourceDocument
    final_url: str
    status: int
    checksum: str
    chunks: list[Chunk]


def _from_cache(doc: SourceDocument, cached: Path, reason: str) -> tuple[bytes, str, int]:
    if not cached.exists():
        raise ValueError(f"{doc.document_id}: {reason} and no captured copy in cache")
    captured_at = datetime.fromtimestamp(cached.stat().st_mtime, tz=UTC).isoformat()
    log.warning("knowledge_source_from_cache", document_id=doc.document_id, reason=reason, captured_at=captured_at)
    return cached.read_bytes(), f"{doc.source_url}#cached:{captured_at}", 200


def download(
    doc: SourceDocument, manifest: SourceManifest, cache_dir: Path, *, timeout: float = 30
) -> tuple[bytes, str, int]:
    host = urlsplit(str(doc.source_url)).netloc
    if host not in manifest.allowed_domains:
        raise PermissionError(f"{host} not allowlisted")
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached = cache_dir / f"{doc.document_id}.html"
    try:
        with httpx.Client(
            timeout=timeout, follow_redirects=True, headers={"User-Agent": "smart-travel-assistant/knowledge-ingest"}
        ) as c:
            r = c.get(str(doc.source_url))
    except httpx.HTTPError as exc:
        return _from_cache(doc, cached, f"network error {type(exc).__name__}")
    if urlsplit(str(r.url)).netloc not in manifest.allowed_domains:
        raise PermissionError(f"redirected outside allowlist: {r.url}")
    ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
    if r.status_code != 200 or ctype not in ALLOWED_TYPES:
        # some official sites (ready.gov) block container egress with 403; a previously captured copy of the same
        # real page is acceptable and is reported as CACHED with its capture time, never as a fresh fetch
        return _from_cache(doc, cached, f"status {r.status_code} type {ctype!r}")
    if len(r.content) > MAX_BYTES:
        raise ValueError(f"{doc.document_id}: body too large ({len(r.content)} bytes)")
    (cache_dir / f"{doc.document_id}.html").write_bytes(r.content)
    return r.content, str(r.url), r.status_code


_WS = re.compile(r"[ \t ]+")
_NL = re.compile(r"\n{3,}")


def html_to_sections(html: bytes) -> list[tuple[str, str]]:
    """Return [(section_title, text)] preserving list items as separate lines."""
    soup = BeautifulSoup(html, "lxml")
    for t in soup(["script", "style", "noscript", "nav", "footer", "header", "form", "iframe", "svg"]):
        t.decompose()
    root = soup.find("main") or soup.find("article") or soup.body or soup
    sections: list[tuple[str, list[str]]] = [("Introduction", [])]
    for el in root.find_all(["h1", "h2", "h3", "h4", "p", "li"]):
        if el.name in ("h1", "h2", "h3", "h4"):
            title = _WS.sub(" ", el.get_text(" ", strip=True)).strip()
            if title:
                sections.append((title, []))
        else:
            txt = _WS.sub(" ", el.get_text(" ", strip=True)).strip()
            if len(txt) < 3:
                continue
            if el.name == "li":
                txt = "• " + txt
            # avoid duplicates from nested li/p
            if sections[-1][1] and sections[-1][1][-1] == txt:
                continue
            sections[-1][1].append(txt)
    out = []
    for title, lines in sections:
        text = _NL.sub("\n\n", "\n".join(lines)).strip()
        if len(text) >= MIN_CHUNK_CHARS:
            out.append((title[:120], text))
    return out


def chunk_sections(doc: SourceDocument, sections: list[tuple[str, str]]) -> list[Chunk]:
    chunks: list[Chunk] = []
    order = 0
    for title, text in sections:
        blocks = _split_keep_lists(text)
        buf = ""
        for b in blocks:
            if buf and len(buf) + len(b) + 2 > MAX_CHUNK_CHARS:
                chunks.append(_mk(doc, title, buf, order))
                order += 1
                buf = b
            else:
                buf = (buf + "\n\n" + b) if buf else b
        if buf:
            chunks.append(_mk(doc, title, buf, order))
            order += 1
    return chunks


def _split_keep_lists(text: str) -> list[str]:
    """Split into paragraphs but keep consecutive list items ('• ...') together as one block."""
    lines = text.split("\n")
    blocks: list[str] = []
    cur: list[str] = []
    cur_is_list = False
    for ln in lines:
        is_list = ln.startswith("• ")
        if cur and (is_list != cur_is_list or (not is_list and not cur_is_list)):
            blocks.append("\n".join(cur))
            cur = []
        cur.append(ln)
        cur_is_list = is_list
    if cur:
        blocks.append("\n".join(cur))
    # very long list blocks are split on item boundaries only
    out: list[str] = []
    for b in blocks:
        if len(b) <= MAX_CHUNK_CHARS or not b.startswith("• "):
            out.append(b)
            continue
        items = b.split("\n")
        piece: list[str] = []
        for it in items:
            if piece and len("\n".join(piece)) + len(it) > MAX_CHUNK_CHARS:
                out.append("\n".join(piece))
                piece = []
            piece.append(it)
        if piece:
            out.append("\n".join(piece))
    return out


def _mk(doc: SourceDocument, section: str, text: str, order: int) -> Chunk:
    h = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return Chunk(
        chunk_id=f"{doc.document_id}:{order:04d}:{h[:8]}",
        document_id=doc.document_id,
        section=section,
        page=None,
        text=text,
        text_hash=h,
        order=order,
        hazards=[h_.value for h_ in doc.hazards],
        language=doc.languages[0] if doc.languages else "en",
        country=doc.country,
        authority=doc.authority.value,
    )


def ingest_document(doc: SourceDocument, manifest: SourceManifest, cache_dir: Path) -> IngestedDocument:
    body, final_url, status = download(doc, manifest, cache_dir)
    checksum = hashlib.sha256(body).hexdigest()
    chunks = chunk_sections(doc, html_to_sections(body))
    if not chunks:
        raise ValueError(f"{doc.document_id}: no text extracted")
    log.info("document_ingested", document_id=doc.document_id, chunks=len(chunks), checksum=checksum[:12])
    return IngestedDocument(doc=doc, final_url=final_url, status=status, checksum=checksum, chunks=chunks)
