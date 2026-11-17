"""Small, dependency-free text utilities (tokenisation, sentence split, hashing)."""

from __future__ import annotations

import hashlib
import re
import unicodedata

_WORD_RE = re.compile(r"[a-z0-9]+(?:[.'-][a-z0-9]+)*")
_TOKEN_RE = re.compile(r"\w+|[^\w\s]", re.UNICODE)
_SENT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[])")

STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "been",
        "but",
        "by",
        "can",
        "could",
        "did",
        "do",
        "does",
        "for",
        "from",
        "had",
        "has",
        "have",
        "how",
        "i",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "me",
        "my",
        "of",
        "on",
        "or",
        "our",
        "should",
        "so",
        "than",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "these",
        "they",
        "this",
        "to",
        "too",
        "us",
        "was",
        "we",
        "were",
        "what",
        "when",
        "where",
        "which",
        "who",
        "whom",
        "why",
        "will",
        "with",
        "would",
        "you",
        "your",
        "about",
        "any",
        "all",
        "also",
        "am",
        "may",
        "might",
        "must",
        "much",
        "many",
        "per",
        "via",
        "vs",
    ]
)


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def words(text: str) -> list[str]:
    """Lower-cased word tokens (keeps decimals/hyphenated words together)."""
    return _WORD_RE.findall(text.lower())


def content_words(text: str) -> list[str]:
    return [w for w in words(text) if w not in STOPWORDS]


def count_tokens(text: str) -> int:
    """Approximate LLM token count (~1.3 tokens per word-ish unit)."""
    return int(len(_TOKEN_RE.findall(text)) * 1.15) + 1


_BLOCK_START = re.compile(r"^\s*(?:[-*+]\s|\||\d+[.)]\s|#)")


def unwrap(text: str) -> list[str]:
    """Join hard-wrapped lines into logical lines; list items, table rows and headings stay separate."""
    lines: list[str] = []
    for raw in text.split("\n"):
        line = raw.strip()
        if not line:
            lines.append("")
            continue
        if lines and lines[-1] and not _BLOCK_START.match(line) and not lines[-1].startswith(("|", "#")):
            lines[-1] = f"{lines[-1]} {line}"
        else:
            lines.append(line)
    return [ln for ln in lines if ln]


def split_sentences(text: str) -> list[str]:
    parts: list[str] = []
    for line in unwrap(text):
        parts.extend(s.strip() for s in _SENT_RE.split(line) if s.strip())
    return parts


def sha256_hex(data: str | bytes) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def doc_id_for(source_uri: str) -> str:
    return sha256_hex(source_uri)[:16]


def chunk_id_for(source_uri: str, index: int) -> str:
    return f"{doc_id_for(source_uri)}-{index:04d}"
