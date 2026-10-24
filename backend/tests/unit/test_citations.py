"""Citation normalization and validation."""

import uuid

import pytest

from app.agent.citations import normalize_markers, validate_citations
from app.retrieval.types import EvidenceChunk


def chunk(n: int, uri: str | None = None) -> EvidenceChunk:
    return EvidenceChunk(
        chunk_id=uuid.UUID(int=n),
        document_id=uuid.UUID(int=99),
        collection_id=uuid.UUID(int=98),
        document_title=f"Doc {n}",
        source_uri=uri,
        text=f"Passage number {n} text.",
        ordinal=n,
        page_number=n,
        section="S",
    )


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [
        ("Limit is 250 EUR【S1】.", "Limit is 250 EUR[S1]."),
        ("Limit【S1†L3-L5】 applies.", "Limit[S1] applies."),
        ("Limit [S1†source] applies.", "Limit [S1] applies."),
        ("Both [S1, S2].", "Both [S1, S2]."),
        ("Plain [S3].", "Plain [S3]."),
        ("A list [1] stays.", "A list [1] stays."),
    ],
)
def test_marker_variants_are_normalized(raw: str, normalized: str) -> None:
    assert normalize_markers(raw) == normalized


def test_full_width_markers_from_gpt_oss_validate() -> None:
    result = validate_citations("Hotels are capped at 250 EUR per night【S1】.", [chunk(1)])
    assert result.text == "Hotels are capped at 250 EUR per night[S1]."
    assert [c.marker for c in result.citations] == ["S1"]
    assert result.supported_ratio == 1.0


def test_grouped_markers_expand_and_unknown_ones_drop() -> None:
    result = validate_citations("Fact one [S1, S9]. Fact two [S2].", [chunk(1), chunk(2)])
    assert result.text == "Fact one [S1]. Fact two [S2]."
    assert result.invalid_markers == ["S9"]
    assert [c.chunk_id for c in result.citations] == [str(uuid.UUID(int=1)), str(uuid.UUID(int=2))]


def test_uncited_sentences_are_labelled_general_context() -> None:
    result = validate_citations("Supported fact [S1]. This is general advice.", [chunk(1)])
    assert [c["supported"] for c in result.claims] == [True, False]
    assert result.supported_ratio == 0.5


def test_citations_carry_location_metadata() -> None:
    [citation] = validate_citations("Fact [S1].", [chunk(4)]).citations
    assert (citation.document_title, citation.page_number, citation.section) == ("Doc 4", 4, "S")
    assert citation.snippet == "Passage number 4 text."


def test_only_source_urls_survive() -> None:
    evidence = [chunk(1, uri="https://intranet.example.com/policy")]
    result = validate_citations(
        "See https://intranet.example.com/policy [S1] or https://phish.example.net/login [S1].",
        evidence,
    )
    assert "https://intranet.example.com/policy" in result.text
    assert "phish.example.net" not in result.text
    assert result.removed_urls == ["https://phish.example.net/login"]


@pytest.mark.parametrize(
    "link",
    [
        "[here](//evil.example/phish)",
        "HTTPS://evil.example/phish",
        "www.evil.example/phish",
        "[click](javascript:alert(1))",
        "[docs](https://evil.example/login)",
    ],
)
def test_link_variants_not_from_sources_are_removed(link: str) -> None:
    result = validate_citations(f"Policy applies [S1]. See {link} now [S1].", [chunk(1)])
    assert "evil.example" not in result.text
    assert "javascript" not in result.text
    assert result.removed_urls


def test_markdown_link_to_a_source_url_is_kept() -> None:
    evidence = [chunk(1, uri="https://intranet.example.com/policy")]
    result = validate_citations(
        "Read [the policy](https://intranet.example.com/policy) [S1].", evidence
    )
    assert "(https://intranet.example.com/policy)" in result.text
