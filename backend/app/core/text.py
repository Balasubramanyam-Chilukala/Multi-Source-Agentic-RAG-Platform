"""Small, dependency-free text utilities shared by the mock providers, evidence checks, and
evaluation metrics. Deliberately simple and deterministic."""

import re
import unicodedata

_WORD = re.compile(r"[a-z0-9]+(?:[.'][a-z0-9]+)*")

STOPWORDS = frozenset(
    """
    a about above after again against all am an and any are as at be because been before being
    below between both but by can could did do does doing down during each few for from further
    had has have having he her here hers herself him himself his how i if in into is it its
    itself just me more most my myself no nor not now of off on once only or other our ours
    ourselves out over own same she should so some such than that the their theirs them
    themselves then there these they this those through to too under until up very was we were
    what when where which while who whom why will with would you your yours yourself yourselves
    tell please know give show say much many also may might must shall us via per get got
    """.split()  # noqa: SIM905  (readable as a word block)
)

_SUFFIXES = (
    "ization",
    "ational",
    "ingly",
    "ments",
    "ement",
    "ness",
    "ions",
    "ing",
    "ies",
    "ied",
    "ed",
    "es",
    "ly",
    "s",
)


def normalize_unicode(text: str) -> str:
    return unicodedata.normalize("NFKC", text)


def tokenize(text: str) -> list[str]:
    return _WORD.findall(normalize_unicode(text).lower())


def stem(word: str) -> str:
    """A tiny suffix stripper; good enough to match plural/tense variants in tests and mocks."""
    for suffix in _SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            word = word[: -len(suffix)]
            break
    if word.endswith("e") and len(word) > 4:
        word = word[:-1]
    return word


def content_terms(text: str) -> list[str]:
    """Stemmed, stopword-free terms, in order (duplicates kept)."""
    return [stem(t) for t in tokenize(text) if t not in STOPWORDS and len(t) > 1]


def term_coverage(query: str, passage: str) -> float:
    """Fraction of distinct query content terms that appear in the passage (0..1)."""
    query_terms = set(content_terms(query))
    if not query_terms:
        return 0.0
    return len(query_terms & set(content_terms(passage))) / len(query_terms)


_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE.split(text.strip()) if s.strip()]
