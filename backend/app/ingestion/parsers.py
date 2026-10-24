"""Text extraction for supported formats.

Each parser returns ``ParsedDocument`` with ordered ``Section``s that keep the page number
(PDF) and heading path (Markdown, HTML, DOCX) so chunks can cite precise locations.

Safety: inputs are size-limited by the caller; PDFs are page-limited; DOCX archives are checked
for decompression bombs; HTML is reduced to text (scripts/styles removed, never rendered).
"""

import csv
import io
import json
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import PurePath
from typing import Any

from bs4 import BeautifulSoup

from app.core.text import normalize_unicode

# lxml builds a DOM several times larger than the XML, so keep the uncompressed cap modest.
MAX_DOCX_UNCOMPRESSED = 50 * 1024 * 1024
MAX_DOCX_RATIO = 100
MAX_CSV_ROWS = 20_000
MAX_JSON_ITEMS = 50_000
# Structured formats repeat keys/headers on every value, so their text can be far larger than the
# input (a long key over many list items). Extracted text is capped at this multiple of the input.
MAX_EXPANSION = 4
MIN_OUTPUT_BUDGET = 1_000_000
MAX_KEY_CHARS = 100

SUPPORTED_EXTENSIONS = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".txt": "text",
    ".text": "text",
    ".md": "markdown",
    ".markdown": "markdown",
    ".html": "html",
    ".htm": "html",
    ".csv": "csv",
    ".json": "json",
}
MIME_KINDS = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "text/plain": "text",
    "text/markdown": "markdown",
    "text/x-markdown": "markdown",
    "text/html": "html",
    "application/xhtml+xml": "html",
    "text/csv": "csv",
    "application/json": "json",
}


class ParseError(Exception):
    """The file is corrupt, encrypted, or not what its type claims."""

    code = "parse_error"


class UnsupportedFileTypeError(ParseError):
    code = "unsupported_type"


class NoExtractableTextError(ParseError):
    """Parsed fine but contains no text, e.g. a scanned PDF that needs OCR."""

    code = "no_text"


class _Budget:
    """Character budget for extracted text; raises once exceeded."""

    def __init__(self, input_chars: int) -> None:
        self.remaining = max(MAX_EXPANSION * input_chars, MIN_OUTPUT_BUDGET)

    def spend(self, text: str) -> str:
        self.remaining -= len(text) + 1
        if self.remaining < 0:
            raise ParseError("The document expands to too much text to index safely")
        return text


@dataclass
class Section:
    text: str
    page: int | None = None
    heading: str | None = None


@dataclass
class ParsedDocument:
    kind: str
    title: str | None
    sections: list[Section]
    page_count: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def detect_kind(filename: str | None, mime_type: str | None, content: bytes) -> str:
    """Decide the format from extension/MIME, then verify it against the file's magic bytes."""
    kind = None
    if filename:
        kind = SUPPORTED_EXTENSIONS.get(PurePath(filename).suffix.lower())
    if kind is None and mime_type:
        kind = MIME_KINDS.get(mime_type.split(";")[0].strip().lower())
    if kind is None:
        raise UnsupportedFileTypeError(
            f"Unsupported file type. Supported: {', '.join(sorted(set(SUPPORTED_EXTENSIONS)))}"
        )
    if kind == "pdf" and not content.lstrip()[:5].startswith(b"%PDF"):
        raise ParseError("File has a .pdf name but is not a PDF")
    if kind == "docx" and not content.startswith(b"PK"):
        raise ParseError("File has a .docx name but is not a DOCX archive")
    if kind not in ("pdf", "docx") and b"\x00" in content[:4096]:
        raise ParseError("Text file appears to be binary")
    return kind


def decode_text(content: bytes) -> str:
    for encoding in (
        ("utf-8-sig", "utf-16") if content[:2] in (b"\xff\xfe", b"\xfe\xff") else ("utf-8-sig",)
    ):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    # Windows-1252 decodes any byte sequence; used as a last resort for legacy text files.
    return content.decode("cp1252", errors="replace")


def _parse_pdf(content: bytes, max_pages: int) -> ParsedDocument:
    from pypdf import PdfReader  # noqa: PLC0415  (heavy import, only when needed)
    from pypdf.errors import PdfReadError  # noqa: PLC0415

    try:
        reader = PdfReader(io.BytesIO(content))
        if reader.is_encrypted:
            raise ParseError("Encrypted PDFs are not supported")
        page_count = len(reader.pages)
        if page_count > max_pages:
            raise ParseError(f"PDF has {page_count} pages; the limit is {max_pages}")
        sections = []
        for number, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""
            if text.strip():
                sections.append(Section(text=text, page=number))
        title = None
        if reader.metadata and reader.metadata.title:
            title = str(reader.metadata.title).strip() or None
    except ParseError:
        raise
    except (PdfReadError, ValueError, KeyError, TypeError) as exc:
        raise ParseError(f"Could not read PDF ({type(exc).__name__})") from exc
    if not sections:
        raise NoExtractableTextError(
            "No extractable text found; the PDF is probably scanned and needs OCR (not enabled)"
        )
    return ParsedDocument("pdf", title, sections, page_count=page_count)


def _check_zip_bomb(content: bytes) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            total = sum(info.file_size for info in archive.infolist())
            compressed = sum(info.compress_size for info in archive.infolist()) or 1
    except zipfile.BadZipFile as exc:
        raise ParseError("DOCX archive is corrupt") from exc
    if total > MAX_DOCX_UNCOMPRESSED or total / compressed > MAX_DOCX_RATIO:
        raise ParseError("DOCX archive expands to an unsafe size")


def _parse_docx(content: bytes) -> ParsedDocument:
    _check_zip_bomb(content)
    import docx  # noqa: PLC0415

    try:
        document = docx.Document(io.BytesIO(content))
    except Exception as exc:  # python-docx raises several unrelated types for bad files
        raise ParseError(f"Could not read DOCX ({type(exc).__name__})") from exc

    sections: list[Section] = []
    heading: str | None = None
    buffer: list[str] = []

    def flush() -> None:
        if buffer:
            sections.append(Section(text="\n\n".join(buffer), heading=heading))
            buffer.clear()

    for paragraph in document.paragraphs:
        text = paragraph.text.strip()
        if not text:
            continue
        style = (paragraph.style.name if paragraph.style is not None else "") or ""
        if style.lower().startswith(("heading", "title")):
            flush()
            heading = text
        else:
            buffer.append(text)
    flush()
    for table in document.tables:
        rows = [" | ".join(cell.text.strip() for cell in row.cells) for row in table.rows]
        if any(r.strip(" |") for r in rows):
            sections.append(Section(text="\n".join(rows), heading="Table"))

    title = (document.core_properties.title or "").strip() or None
    if not sections:
        raise NoExtractableTextError("The DOCX file contains no text")
    return ParsedDocument("docx", title, sections)


_MD_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


def _parse_markdown(text: str) -> ParsedDocument:
    sections: list[Section] = []
    path: list[str] = []
    buffer: list[str] = []
    title = None
    in_code = False

    def flush() -> None:
        body = "\n".join(buffer).strip()
        if body:
            sections.append(Section(text=body, heading=" > ".join(path) or None))
        buffer.clear()

    for line in text.splitlines():
        if line.strip().startswith("```"):
            in_code = not in_code
        match = None if in_code else _MD_HEADING.match(line)
        if match:
            flush()
            level, heading = len(match.group(1)), match.group(2).strip()
            path[:] = [*path[: level - 1], heading]
            if title is None and level == 1:
                title = heading
        else:
            buffer.append(line)
    flush()
    return ParsedDocument("markdown", title, sections)


def _parse_html(text: str) -> ParsedDocument:
    soup = BeautifulSoup(text, "html.parser")
    for tag in soup(
        ["script", "style", "noscript", "template", "svg", "iframe", "nav", "footer", "form"]
    ):
        tag.decompose()
    title = soup.title.get_text(" ", strip=True) if soup.title else None
    body = soup.body or soup
    sections: list[Section] = []
    heading: str | None = None
    buffer: list[str] = []
    for element in body.find_all(["h1", "h2", "h3", "h4", "p", "li", "pre", "td", "blockquote"]):
        content = element.get_text(" ", strip=True)
        if not content:
            continue
        if element.name in ("h1", "h2", "h3", "h4"):
            if buffer:
                sections.append(Section(text="\n".join(buffer), heading=heading))
                buffer = []
            heading = content
            title = title or content
        else:
            buffer.append(content)
    if buffer:
        sections.append(Section(text="\n".join(buffer), heading=heading))
    if not sections:
        fallback = body.get_text("\n", strip=True)
        if fallback:
            sections.append(Section(text=fallback))
    return ParsedDocument("html", title, sections)


def _parse_csv(text: str) -> ParsedDocument:
    try:
        dialect: Any = csv.Sniffer().sniff(text[:4096])
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text), dialect)
    try:
        header = next(reader)
    except StopIteration:
        raise NoExtractableTextError("The CSV file is empty") from None
    except csv.Error as exc:
        raise ParseError(f"Malformed CSV ({exc})") from exc
    header = [h.strip()[:MAX_KEY_CHARS] for h in header]
    budget = _Budget(len(text))
    lines = []
    try:
        for count, row in enumerate(reader, start=1):
            if count > MAX_CSV_ROWS:
                raise ParseError(f"CSV has more than {MAX_CSV_ROWS} rows")
            pairs = [f"{h}: {v.strip()}" for h, v in zip(header, row, strict=False) if v.strip()]
            if pairs:
                lines.append(budget.spend(f"Row {count}: " + "; ".join(pairs)))
    except csv.Error as exc:
        raise ParseError(f"Malformed CSV ({exc})") from exc
    if not lines:
        raise NoExtractableTextError("The CSV file has a header but no data rows")
    return ParsedDocument(
        "csv",
        None,
        [Section(text="\n".join(lines))],
        metadata={"columns": header, "rows": len(lines)},
    )


def _parse_text(text: str) -> ParsedDocument:
    return ParsedDocument("text", None, [Section(text=text)])


def _flatten_json(value: Any, path: str, out: list[str], budget: _Budget) -> None:
    if len(out) > MAX_JSON_ITEMS:
        raise ParseError("JSON document is too large")
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key)[:MAX_KEY_CHARS]
            _flatten_json(item, f"{path}.{key_text}" if path else key_text, out, budget)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _flatten_json(item, f"{path}[{index}]", out, budget)
    elif value is not None and str(value).strip():
        out.append(budget.spend(f"{path}: {value}"))


def _parse_json(text: str) -> ParsedDocument:
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ParseError("Malformed JSON") from exc
    lines: list[str] = []
    try:
        _flatten_json(data, "", lines, _Budget(len(text)))
    except RecursionError as exc:
        raise ParseError("JSON is nested too deeply") from exc
    if not lines:
        raise NoExtractableTextError("The JSON document contains no values")
    title = (
        data.get("title") if isinstance(data, dict) and isinstance(data.get("title"), str) else None
    )
    return ParsedDocument("json", title, [Section(text="\n".join(lines))])


def parse_document(
    content: bytes, *, filename: str | None, mime_type: str | None, max_pdf_pages: int = 500
) -> ParsedDocument:
    kind = detect_kind(filename, mime_type, content)
    if kind == "pdf":
        parsed = _parse_pdf(content, max_pdf_pages)
    elif kind == "docx":
        parsed = _parse_docx(content)
    else:
        text = normalize_unicode(decode_text(content))
        parsed = {
            "markdown": _parse_markdown,
            "html": _parse_html,
            "csv": _parse_csv,
            "json": _parse_json,
            "text": _parse_text,
        }[kind](text)
    if not any(s.text.strip() for s in parsed.sections):
        raise NoExtractableTextError("The document contains no extractable text")
    return parsed
