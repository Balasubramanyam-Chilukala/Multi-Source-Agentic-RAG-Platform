"""Citation extraction and validation for generated answers.

Guarantees for every answer that leaves the agent:
- every citation marker refers to a passage that was actually retrieved for this answer;
- unknown markers (e.g. ``[S9]`` when only 4 sources were given) are removed;
- URLs not present in the retrieved sources are removed;
- each sentence is labelled as source-supported (has a valid citation) or general context.

It does not (and cannot) prove that a cited passage entails the sentence; that is measured
offline by the evaluation framework and documented as a limitation.
"""

import re
from dataclasses import dataclass, field

from app.core.text import split_sentences
from app.retrieval.types import EvidenceChunk

MARKER = re.compile(r"\[(S\d+(?:\s*[,;]\s*S\d+)*)\]")
# Models trained on other citation styles emit variants such as "【S1】", "【S1†L3-L5】",
# "[S1†source]" or "[S1, S2]". They are normalized to "[S1]" before validation.
_VARIANT = re.compile(r"[\[【]\s*(S\d+(?:\s*[,;]\s*S\d+)*)\s*(?:†[^\]】]*)?[\]】]")
# Link-like text the model might emit: Markdown links (any target scheme), absolute URLs in any
# case, "www." hosts, and protocol-relative "//host" links. Only retrieved sources' URLs survive.
MD_LINK = re.compile(r"\[([^\]\n]*)\]\(\s*([^)\s]*)[^)]*\)")
URL = re.compile(r"(?i)(?:\bhttps?://|\bwww\.|(?<![\w:/])//[a-z0-9-]+\.)[^\s)\]>\"'<]*")


@dataclass
class Citation:
    marker: str
    chunk_id: str
    document_id: str
    document_title: str
    page_number: int | None
    section: str | None
    source_uri: str | None
    snippet: str

    def to_dict(self) -> dict[str, object]:
        return {
            "marker": self.marker,
            "chunk_id": self.chunk_id,
            "document_id": self.document_id,
            "document_title": self.document_title,
            "page_number": self.page_number,
            "section": self.section,
            "source_uri": self.source_uri,
            "snippet": self.snippet,
        }


@dataclass
class ValidatedAnswer:
    text: str
    citations: list[Citation] = field(default_factory=list)
    invalid_markers: list[str] = field(default_factory=list)
    removed_urls: list[str] = field(default_factory=list)
    claims: list[dict[str, object]] = field(default_factory=list)

    @property
    def supported_ratio(self) -> float:
        if not self.claims:
            return 0.0
        return sum(1 for c in self.claims if c["supported"]) / len(self.claims)


def _snippet(text: str, limit: int = 320) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rsplit(" ", 1)[0] + "…"


def normalize_markers(answer: str) -> str:
    return _VARIANT.sub(lambda m: f"[{m.group(1)}]", answer)


def validate_citations(answer: str, evidence: list[EvidenceChunk]) -> ValidatedAnswer:
    answer = normalize_markers(answer)
    by_marker = {f"S{i}": chunk for i, chunk in enumerate(evidence, start=1)}
    allowed_urls = {c.source_uri for c in evidence if c.source_uri}
    invalid: list[str] = []
    used: dict[str, Citation] = {}

    def replace(match: re.Match[str]) -> str:
        kept = []
        for marker in re.split(r"\s*[,;]\s*", match.group(1)):
            chunk = by_marker.get(marker)
            if chunk is None:
                invalid.append(marker)
                continue
            kept.append(marker)
            used.setdefault(
                marker,
                Citation(
                    marker,
                    str(chunk.chunk_id),
                    str(chunk.document_id),
                    chunk.document_title,
                    chunk.page_number,
                    chunk.section,
                    chunk.source_uri,
                    _snippet(chunk.text),
                ),
            )
        return "".join(f"[{m}]" for m in kept)

    text = MARKER.sub(replace, answer)
    allowed = {u.lower().rstrip("/") for u in allowed_urls}

    def is_allowed(url: str) -> bool:
        return url.lower().rstrip(".,;:!?").rstrip("/") in allowed

    removed_urls: list[str] = []

    def replace_link(match: re.Match[str]) -> str:
        label, target = match.group(1), match.group(2)
        if is_allowed(target):
            return match.group(0)
        removed_urls.append(target)
        return label  # keep the visible text, drop the destination

    def replace_url(match: re.Match[str]) -> str:
        url = match.group(0)
        if is_allowed(url):
            return url
        removed_urls.append(url.rstrip(".,;:!?"))
        trailing = url[len(url.rstrip(".,;:!?")) :]
        return "[link removed]" + trailing

    text = MD_LINK.sub(replace_link, text)
    text = URL.sub(replace_url, text)
    text = re.sub(r"[ \t]+([.,;:])", r"\1", re.sub(r"[ \t]{2,}", " ", text)).strip()

    claims = []
    for block in [b for b in text.split("\n") if b.strip()]:
        for sentence in split_sentences(block) or [block]:
            markers = MARKER.findall(sentence)
            content = MARKER.sub("", sentence).strip(" -*•")
            if len(content) < 3:
                continue
            claims.append(
                {
                    "text": content,
                    "supported": bool(markers),
                    "markers": sorted(
                        {m for group in markers for m in re.split(r"\s*[,;]\s*", group)}
                    ),
                }
            )
    ordered = sorted(used.values(), key=lambda c: int(c.marker[1:]))
    return ValidatedAnswer(text, ordered, invalid, removed_urls, claims)
