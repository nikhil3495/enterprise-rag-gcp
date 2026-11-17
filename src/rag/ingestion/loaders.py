"""Document loaders: Markdown, plain text, HTML (heading-preserving) and PDF (with page numbers).

Every loader emits Markdown-flavoured text so the chunker can split on headings uniformly.
Sources can be a local directory or a ``gs://bucket/prefix``.
"""

from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from rag import storage
from rag.text import normalize, sha256_hex

log = logging.getLogger(__name__)

SUPPORTED = {
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".txt": "text/plain",
    ".html": "text/html",
    ".htm": "text/html",
    ".pdf": "application/pdf",
}


@dataclass
class Segment:
    text: str
    page: int | None = None


@dataclass
class RawDocument:
    source_uri: str
    title: str
    mime: str
    segments: list[Segment] = field(default_factory=list)
    content_hash: str = ""

    @property
    def full_text(self) -> str:
        return "\n\n".join(s.text for s in self.segments)


def _canonical_uri(uri: str) -> str:
    return uri if storage.is_gcs(uri) else PurePosixPath(uri.replace("\\", "/")).as_posix()


def _title_from_name(uri: str) -> str:
    stem = PurePosixPath(uri).stem
    return re.sub(r"[_-]+", " ", stem).strip().title()


def load_markdown(uri: str, data: bytes) -> RawDocument:
    text = normalize(data.decode("utf-8", errors="replace"))
    m = re.search(r"^#\s+(.+)$", text, flags=re.M)
    title = m.group(1).strip() if m else _title_from_name(uri)
    return RawDocument(uri, title, "text/markdown", [Segment(text)])


def load_text(uri: str, data: bytes) -> RawDocument:
    text = normalize(data.decode("utf-8", errors="replace"))
    first = text.split("\n", 1)[0].strip()
    title = first if 0 < len(first) <= 100 else _title_from_name(uri)
    return RawDocument(uri, title, "text/plain", [Segment(text)])


def load_html(uri: str, data: bytes) -> RawDocument:
    from bs4 import BeautifulSoup, Tag

    soup = BeautifulSoup(data, "html.parser")
    for t in soup(["script", "style", "nav", "footer", "noscript"]):
        t.decompose()
    lines: list[str] = []
    body = soup.body or soup
    for el in body.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "tr", "pre"]):
        if not isinstance(el, Tag):
            continue
        if el.name == "tr":
            cells = [c.get_text(" ", strip=True) for c in el.find_all(["th", "td"])]
            if cells:
                lines.append("| " + " | ".join(cells) + " |")
            continue
        if el.name == "li" and el.find_parent("li") is not None:
            continue
        text = el.get_text(" ", strip=True)
        if not text:
            continue
        if el.name and el.name[0] == "h":
            lines.extend(["", "#" * int(el.name[1]) + " " + text, ""])
        elif el.name == "li":
            lines.append("- " + text)
        else:
            lines.extend([text, ""])
    text = normalize("\n".join(lines))
    title_tag = soup.find("title")
    h1 = soup.find("h1")
    title = (
        title_tag.get_text(strip=True)
        if title_tag
        else h1.get_text(strip=True)
        if h1
        else _title_from_name(uri)
    )
    if not re.search(r"^#\s+", text, flags=re.M):
        text = f"# {title}\n\n{text}"
    return RawDocument(uri, title, "text/html", [Segment(text)])


def load_pdf(uri: str, data: bytes) -> RawDocument:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    segments = []
    for i, page in enumerate(reader.pages, start=1):
        text = normalize(page.extract_text() or "")
        if text:
            segments.append(Segment(text, page=i))
    meta_title = (reader.metadata.title if reader.metadata else None) or ""
    first_line = segments[0].text.split("\n", 1)[0].strip() if segments else ""
    title = meta_title.strip() or first_line or _title_from_name(uri)
    return RawDocument(uri, title, "application/pdf", segments)


LOADERS = {
    "text/markdown": load_markdown,
    "text/plain": load_text,
    "text/html": load_html,
    "application/pdf": load_pdf,
}


def load_document(uri: str) -> RawDocument | None:
    suffix = PurePosixPath(uri).suffix.lower()
    mime = SUPPORTED.get(suffix)
    if mime is None:
        return None
    data = storage.read_bytes(uri)
    doc = LOADERS[mime](_canonical_uri(uri), data)
    doc.content_hash = sha256_hex(data)
    return doc


def iter_source_uris(source: str) -> list[str]:
    return [u for u in storage.list_files(source) if PurePosixPath(u).suffix.lower() in SUPPORTED]


def load_source(source: str) -> list[RawDocument]:
    docs = []
    for uri in iter_source_uris(source):
        try:
            doc = load_document(uri)
        except Exception:
            log.exception("failed to load document", extra={"uri": uri})
            continue
        if doc and doc.segments:
            docs.append(doc)
    return docs
