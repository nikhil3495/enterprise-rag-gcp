from __future__ import annotations

from pathlib import Path

import pytest

from rag.ingestion.chunker import Chunker
from rag.ingestion.loaders import RawDocument, Segment, load_document, load_html, load_source, load_text
from rag.text import chunk_id_for, count_tokens, doc_id_for, split_sentences, unwrap

ROOT = Path(__file__).resolve().parents[1]


def md_doc(text: str, uri: str = "data/docs/x.md") -> RawDocument:
    return RawDocument(uri, "Fallback", "text/markdown", [Segment(text)], "h")


def test_heading_paths_and_title() -> None:
    doc = md_doc(
        "# Handbook\n\nIntro text here.\n\n## Leave\n\n### Accrual\n\nYou accrue 1.67 days.\n\n## Remote\n\nThree days."
    )
    chunks = Chunker(350, 60).chunk(doc)
    assert [c.section for c in chunks] == ["", "Leave > Accrual", "Remote"]
    assert all(c.title == "Handbook" for c in chunks)
    assert chunks[1].contextual_text.startswith("Handbook > Leave > Accrual\n")


def test_chunk_ids_are_stable_and_derived_from_source() -> None:
    doc = md_doc("# T\n\n## A\n\nalpha.\n\n## B\n\nbeta.")
    chunks = Chunker().chunk(doc)
    assert chunks[0].chunk_id == f"{doc_id_for('data/docs/x.md')}-0000"
    assert chunks[1].chunk_id == chunk_id_for("data/docs/x.md", 1)
    assert len(doc_id_for("data/docs/x.md")) == 16


def test_long_sections_split_with_overlap_and_token_bound() -> None:
    sentences = " ".join(f"Sentence number {i} describes policy detail {i}." for i in range(200))
    doc = md_doc(f"# T\n\n## Big\n\n{sentences}")
    chunks = Chunker(max_tokens=120, overlap_tokens=30).chunk(doc)
    assert len(chunks) > 3
    assert all(c.token_count <= 120 for c in chunks)
    assert all(c.section == "Big" for c in chunks)
    # overlap: the start of chunk n+1 repeats the tail of chunk n
    tail = split_sentences(chunks[0].text)[-1]
    assert tail in chunks[1].text


def test_oversized_sentence_falls_back_to_word_windows() -> None:
    doc = md_doc("# T\n\n" + " ".join(["word"] * 1000))
    chunks = Chunker(max_tokens=100, overlap_tokens=10).chunk(doc)
    assert len(chunks) >= 8 and all(c.token_count <= 100 for c in chunks)


def test_overlap_must_be_smaller_than_size() -> None:
    with pytest.raises(ValueError):
        Chunker(50, 50)


def test_pdf_pages_and_numbered_headings() -> None:
    doc = load_document(str(ROOT / "data" / "docs" / "data_retention_policy.pdf"))
    assert doc is not None and doc.mime == "application/pdf"
    assert [s.page for s in doc.segments] == [1, 2]
    chunks = Chunker().chunk(doc)
    backups = next(c for c in chunks if "35 days" in c.text)
    assert backups.page == 2
    assert backups.section.endswith("3.7 Backups")
    assert backups.section.startswith("3 Retention Schedule")


def test_html_loader_preserves_headings_lists_and_tables() -> None:
    html = b"""<html><head><title>Travel</title><script>x()</script></head><body>
    <h1>Travel Policy</h1><h2>Air</h2><ul><li>Economy under 6 hours</li></ul>
    <h2>Per diem</h2><table><tr><th>Tier</th><th>USD</th></tr><tr><td>Tier 1</td><td>$95</td></tr></table></body></html>"""
    doc = load_html("x.html", html)
    assert doc.title == "Travel"
    assert "## Air" in doc.full_text and "- Economy under 6 hours" in doc.full_text
    assert "| Tier 1 | $95 |" in doc.full_text and "x()" not in doc.full_text
    chunks = Chunker().chunk(doc)
    assert {c.section for c in chunks} == {"Air", "Per diem"}


def test_text_loader_title_from_first_line() -> None:
    doc = load_text("faq.txt", b"IT FAQ\n\nHow? Like this.")
    assert doc.title == "IT FAQ"


def test_load_source_reads_all_supported_types() -> None:
    docs = load_source(str(ROOT / "data" / "docs"))
    kinds = {d.mime for d in docs}
    assert kinds == {"text/markdown", "text/plain", "text/html", "application/pdf"}
    assert all("\\" not in d.source_uri for d in docs)
    assert all(len(d.content_hash) == 64 for d in docs)


def test_unwrap_joins_wrapped_lines_but_keeps_lists() -> None:
    lines = unwrap("A sentence that\nwraps here.\n- item one\n- item two\n| a | b |")
    assert lines == ["A sentence that wraps here.", "- item one", "- item two", "| a | b |"]
    assert count_tokens("hello world") >= 2
