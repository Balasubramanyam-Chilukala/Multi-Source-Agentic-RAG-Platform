"""Normalization and chunking.

Chunks never span sections, so every chunk has a single page/heading to cite. Within a section
text is split on paragraph, then sentence, then word boundaries to approach ``chunk_size``
characters, with ``chunk_overlap`` characters carried into the next chunk for context.
"""

import re
from dataclasses import dataclass

from app.core.text import normalize_unicode, split_sentences
from app.ingestion.parsers import Section

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_HYPHEN_BREAK = re.compile(r"(\w)-\n(\w)")
_SPACES = re.compile(r"[ \t ]+")
_BLANK_LINES = re.compile(r"\n{3,}")


def normalize_text(text: str) -> str:
    text = normalize_unicode(text).replace("\r\n", "\n").replace("\r", "\n")
    text = _CONTROL.sub("", text)
    text = _HYPHEN_BREAK.sub(r"\1\2", text)  # re-join words hyphenated across PDF lines
    text = _SPACES.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    return _BLANK_LINES.sub("\n\n", text).strip()


@dataclass(frozen=True)
class TextChunk:
    ordinal: int
    text: str
    page: int | None
    section: str | None

    @property
    def token_estimate(self) -> int:
        return len(self.text.split())


def _units(text: str, size: int) -> list[str]:
    """Split into pieces no longer than ``size``: paragraphs, then sentences, then words."""
    units: list[str] = []
    for paragraph in (p.strip() for p in text.split("\n\n")):
        if not paragraph:
            continue
        if len(paragraph) <= size:
            units.append(paragraph)
            continue
        for sentence in split_sentences(paragraph) or [paragraph]:
            if len(sentence) <= size:
                units.append(sentence)
                continue
            words, current = sentence.split(), ""
            for word in words:
                if current and len(current) + 1 + len(word) > size:
                    units.append(current)
                    current = word
                else:
                    current = f"{current} {word}".strip()
            if current:
                units.append(current)
    return units


def _overlap_tail(text: str, overlap: int) -> str:
    if overlap <= 0 or len(text) <= overlap:
        return text if overlap > 0 else ""
    tail = text[-overlap:]
    # Start the overlap at a word boundary so chunks don't begin mid-word.
    space = tail.find(" ")
    return tail[space + 1 :] if 0 <= space < len(tail) - 1 else tail


def chunk_sections(
    sections: list[Section], *, chunk_size: int, chunk_overlap: int
) -> list[TextChunk]:
    if chunk_overlap * 2 > chunk_size:
        raise ValueError("chunk_overlap must be at most half of chunk_size")
    chunks: list[TextChunk] = []
    for section in sections:
        text = normalize_text(section.text)
        if not text:
            continue
        current = ""
        # Units leave room for the overlap tail plus a separator, so chunks never exceed chunk_size.
        for unit in _units(text, chunk_size - chunk_overlap - 2):
            separator = "\n\n" if current else ""
            if current and len(current) + len(separator) + len(unit) > chunk_size:
                chunks.append(TextChunk(len(chunks), current, section.page, section.heading))
                current = _overlap_tail(current, chunk_overlap)
                separator = " " if current else ""
            current = f"{current}{separator}{unit}"
        if current:
            chunks.append(TextChunk(len(chunks), current, section.page, section.heading))
    return chunks
