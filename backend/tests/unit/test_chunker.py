import itertools

import pytest

from app.ingestion.chunker import chunk_sections, normalize_text
from app.ingestion.parsers import Section


def test_normalize_joins_hyphenated_words_and_strips_controls() -> None:
    raw = "Reimburse-\nment is pro-\ncessed\x07 within   five  days.\r\n\r\n\r\n\r\nNext."
    assert normalize_text(raw) == "Reimbursement is processed within five days.\n\nNext."


def test_short_section_is_one_chunk_with_metadata() -> None:
    chunks = chunk_sections(
        [Section("Fly economy.", page=3, heading="Travel")], chunk_size=200, chunk_overlap=20
    )
    assert len(chunks) == 1
    assert (chunks[0].ordinal, chunks[0].text, chunks[0].page, chunks[0].section) == (
        0,
        "Fly economy.",
        3,
        "Travel",
    )


def test_chunks_respect_size_and_overlap() -> None:
    sentences = [f"Sentence number {i} talks about policy item {i}." for i in range(60)]
    chunks = chunk_sections([Section(" ".join(sentences))], chunk_size=300, chunk_overlap=60)
    assert len(chunks) > 5
    assert all(len(c.text) <= 300 for c in chunks)
    for previous, current in itertools.pairwise(chunks):
        # The tail of one chunk reappears at the start of the next.
        assert current.text[:20] in previous.text
    # Nothing is lost: every sentence appears in some chunk.
    combined = " ".join(c.text for c in chunks)
    assert all(s in combined for s in sentences)


def test_chunks_never_span_sections() -> None:
    sections = [Section("Alpha " * 10, page=1), Section("Beta " * 10, page=2)]
    chunks = chunk_sections(sections, chunk_size=500, chunk_overlap=50)
    assert [c.page for c in chunks] == [1, 2]
    assert [c.ordinal for c in chunks] == [0, 1]


def test_very_long_word_runs_are_split() -> None:
    chunks = chunk_sections(
        [Section("x" * 50 + " " + "word " * 400)], chunk_size=200, chunk_overlap=20
    )
    assert all(len(c.text) <= 200 for c in chunks)


def test_empty_sections_produce_no_chunks() -> None:
    assert chunk_sections([Section("  \n\n ")], chunk_size=200, chunk_overlap=20) == []


def test_rejects_excessive_overlap() -> None:
    with pytest.raises(ValueError, match="half"):
        chunk_sections([Section("text")], chunk_size=200, chunk_overlap=150)
