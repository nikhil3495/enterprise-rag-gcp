"""Structure-aware chunking.

1. Split on Markdown headings (HTML is converted to Markdown headings; numbered PDF headings such as
   ``3.2 Backups`` are detected too), tracking a ``title > section`` path.
2. Inside a section, pack paragraphs into chunks of ~``max_tokens``; oversized paragraphs fall back to
   sentences, oversized sentences to word windows.
3. Consecutive chunks of the same section overlap by ~``overlap_tokens`` (trailing sentences carried).
Chunks never cross a section boundary; PDF page numbers are preserved (page of the chunk start).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from rag.ingestion.loaders import RawDocument
from rag.models import Chunk
from rag.text import chunk_id_for, count_tokens, doc_id_for, split_sentences

_MD_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_NUM_HEADING = re.compile(r"^(\d+(?:\.\d+)*)\.?\s+([A-Z][A-Za-z0-9 ,&/()'\-]{2,80})$")


@dataclass
class _Unit:
    text: str
    section: str
    page: int | None


class Chunker:
    def __init__(self, max_tokens: int = 350, overlap_tokens: int = 60) -> None:
        if overlap_tokens >= max_tokens:
            raise ValueError("overlap must be smaller than chunk size")
        self.max_tokens = max_tokens
        self.overlap_tokens = overlap_tokens

    # ------------------------------------------------------------------------------------------
    def _units(self, doc: RawDocument) -> tuple[str, list[_Unit]]:
        title = doc.title
        stack: list[tuple[int, str]] = []
        units: list[_Unit] = []
        para: list[str] = []
        page: int | None = None
        seen_title = False

        def section() -> str:
            return " > ".join(h for _, h in stack)

        def flush() -> None:
            if para:
                text = "\n".join(para).strip()
                if text:
                    units.append(_Unit(text, section(), page))
                para.clear()

        for seg in doc.segments:
            page = seg.page
            for raw in seg.text.split("\n"):
                line = raw.rstrip()
                level, heading = self._heading(line, doc.mime)
                if heading is not None:
                    flush()
                    if level == 1 and not seen_title:
                        title, seen_title = heading, True
                        continue
                    while stack and stack[-1][0] >= level:
                        stack.pop()
                    stack.append((level, heading))
                    continue
                if not line.strip():
                    flush()
                    continue
                if (
                    line.lstrip().startswith(("- ", "* ", "|"))
                    and para
                    and not para[-1].lstrip().startswith(("- ", "* ", "|"))
                ):
                    flush()
                para.append(line)
            flush()  # paragraphs never span pages
        return title, units

    @staticmethod
    def _heading(line: str, mime: str) -> tuple[int, str | None]:
        m = _MD_HEADING.match(line)
        if m:
            return len(m.group(1)), m.group(2).strip()
        if mime == "application/pdf":
            n = _NUM_HEADING.match(line.strip())
            if n:
                return n.group(1).count(".") + 2, f"{n.group(1)} {n.group(2).strip()}"
        return 0, None

    # ------------------------------------------------------------------------------------------
    def _pieces(self, text: str) -> list[str]:
        """Break a unit into pieces each <= max_tokens."""
        if count_tokens(text) <= self.max_tokens:
            return [text]
        pieces: list[str] = []
        for sent in split_sentences(text):
            if count_tokens(sent) <= self.max_tokens:
                pieces.append(sent)
                continue
            words = sent.split()
            step = max(1, int(self.max_tokens / 1.4))
            pieces.extend(" ".join(words[i : i + step]) for i in range(0, len(words), step))
        return pieces

    def _overlap_tail(self, text: str) -> str:
        tail: list[str] = []
        total = 0
        for sent in reversed(split_sentences(text)):
            t = count_tokens(sent)
            if total + t > self.overlap_tokens:
                break
            tail.insert(0, sent)
            total += t
        return " ".join(tail)

    def chunk(self, doc: RawDocument) -> list[Chunk]:
        title, units = self._units(doc)
        doc_id = doc_id_for(doc.source_uri)
        drafts: list[tuple[str, str, int | None]] = []  # (text, section, page)

        cur: list[str] = []
        cur_section: str | None = None
        cur_page: int | None = None
        cur_tokens = 0

        def emit() -> None:
            nonlocal cur, cur_tokens
            if cur:
                drafts.append(("\n".join(cur).strip(), cur_section or "", cur_page))
            cur, cur_tokens = [], 0

        for unit in units:
            if unit.section != cur_section:
                emit()
                cur_section, cur_page = unit.section, unit.page
            for piece in self._pieces(unit.text):
                t = count_tokens(piece)
                if cur and cur_tokens + t > self.max_tokens:
                    prev = "\n".join(cur)
                    emit()
                    cur_page = unit.page
                    tail = self._overlap_tail(prev)
                    if tail and count_tokens(tail) + t <= self.max_tokens:
                        cur, cur_tokens = [tail], count_tokens(tail)
                if not cur:
                    cur_page = unit.page
                cur.append(piece)
                cur_tokens += t
        emit()

        return [
            Chunk(
                chunk_id=chunk_id_for(doc.source_uri, i),
                doc_id=doc_id,
                source_uri=doc.source_uri,
                title=title,
                section=section,
                text=text,
                page=page,
                index=i,
                token_count=count_tokens(text),
            )
            for i, (text, section, page) in enumerate(drafts)
            if text
        ]
