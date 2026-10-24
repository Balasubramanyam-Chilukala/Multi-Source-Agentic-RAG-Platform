"""Deterministic mock providers for tests, CI, offline development, and labelled demo mode.

- ``HashingEmbeddings``: feature-hashed bag of stemmed unigrams + bigrams. It captures lexical
  overlap only (no semantics), so dense retrieval quality with it is a lower bound, and every
  result produced with it is labelled ``provider=mock``.
- ``MockReranker``: query-term coverage.
- ``MockChat``: reads the task from the system prompt (``TASK: <name>``) and answers
  extractively from the supplied sources. It never invents content, which makes agent tests
  and evaluation runs reproducible without network access.
"""

import hashlib
import itertools
import json
import math
import re
from collections import Counter
from collections.abc import AsyncIterator

from app.core.text import STOPWORDS, content_terms, split_sentences, term_coverage, tokenize
from app.providers.base import ChatMessage, ChatResult, ChatStreamChunk, InputType, Usage

TASK_HEADER = re.compile(r"^TASK:\s*(\w+)", re.MULTILINE)
SOURCE_BLOCK = re.compile(r'<source id="(S\d+)"[^>]*>\n?(.*?)\n?</source>', re.DOTALL)
QUESTION_LINE = re.compile(r"^(?:Question|User question):\s*(.+)$", re.MULTILINE)
ARITHMETIC = re.compile(r"^[\d\s.+\-*/%()^]+$")
GREETINGS = {"hi", "hello", "hey", "thanks", "thank", "morning", "afternoon", "evening"}
LIST_DOCS = re.compile(
    r"\b(what|which|list)\b.*\b(documents?|files?|sources?)\b.*\b(have|available|uploaded|connected)\b",
    re.IGNORECASE,
)
SUMMARIZE = re.compile(
    r"^\s*summari[sz]e\s+(?:the\s+)?(?:document\s+)?[\"']?(.+?)[\"']?\s*$", re.IGNORECASE
)
PRONOUN_ONLY = {"it", "that", "this", "they", "those", "these", "them"}

INSUFFICIENT = "INSUFFICIENT_EVIDENCE"


class HashingEmbeddings:
    name = "mock"
    model = "hashing-embed"
    version = "v1"

    def __init__(self, dimension: int = 384) -> None:
        self.dimension = dimension

    def _vector(self, text: str) -> list[float]:
        terms = content_terms(text)
        features = Counter(terms)
        features.update(f"{a}_{b}" for a, b in itertools.pairwise(terms))
        vector = [0.0] * self.dimension
        for feature, count in features.items():
            digest = hashlib.blake2b(feature.encode(), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "little") % self.dimension
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[index] += sign * (1.0 + math.log(count))
        norm = math.sqrt(sum(v * v for v in vector))
        if norm == 0:
            # A constant vector keeps cosine distance defined for empty/stopword-only text.
            return [1.0 / math.sqrt(self.dimension)] * self.dimension
        return [v / norm for v in vector]

    async def embed(self, texts: list[str], *, input_type: InputType) -> list[list[float]]:
        return [self._vector(t) for t in texts]


class MockReranker:
    name = "mock"
    model = "term-coverage"

    async def rerank(self, query: str, passages: list[str]) -> list[float]:
        return [term_coverage(query, p) * 10 - 5 for p in passages]


def _question(text: str) -> str:
    match = QUESTION_LINE.search(text)
    return match.group(1).strip() if match else text.strip()


def _route(user_text: str) -> dict[str, object]:
    question = _question(user_text)
    words = tokenize(question)
    if ARITHMETIC.match(question.replace("x", "*")) and any(c.isdigit() for c in question):
        return {"action": "calculate", "expression": question}
    if words and len(words) <= 4 and set(words) & GREETINGS:
        return {"action": "direct", "reason": "greeting"}
    if LIST_DOCS.search(question):
        return {"action": "list_documents"}
    summarize = SUMMARIZE.match(question)
    if summarize:
        return {"action": "summarize_document", "document_title": summarize.group(1)}
    content = [w for w in words if w not in STOPWORDS]
    if not content or (
        len(words) <= 3 and set(words) & PRONOUN_ONLY and "History: none" in user_text
    ):
        return {
            "action": "clarify",
            "clarification_question": "Could you say more about what you are looking for?",
        }
    return {"action": "retrieve", "search_query": question}


def _sources(text: str) -> list[tuple[str, str]]:
    return [(sid, body.strip()) for sid, body in SOURCE_BLOCK.findall(text)]


def _generate(user_text: str) -> str:
    question = _question(user_text)
    scored = []
    for sid, body in _sources(user_text):
        sentences = split_sentences(body) or [body]
        best = max(sentences, key=lambda s: term_coverage(question, s))
        scored.append((term_coverage(question, body), term_coverage(question, best), sid, best))
    relevant = [item for item in scored if item[0] >= 0.5]
    if not relevant:
        return INSUFFICIENT
    relevant.sort(key=lambda item: (-item[0], -item[1], item[2]))
    lines = []
    seen = set()
    for _, _, sid, sentence in relevant[:2]:
        if sentence not in seen:
            seen.add(sentence)
            lines.append(f"{sentence} [{sid}]")
    return " ".join(lines)


def _summarize(user_text: str) -> str:
    sources = _sources(user_text)
    if not sources:
        return INSUFFICIENT
    parts = []
    for sid, body in sources[:4]:
        first = (split_sentences(body) or [body])[0]
        parts.append(f"- {first} [{sid}]")
    return "Summary of the document:\n" + "\n".join(parts)


def _refine(user_text: str) -> str:
    question = _question(user_text)
    keywords = [w for w in tokenize(question) if w not in STOPWORDS]
    return json.dumps({"search_query": " ".join(keywords) or question})


class MockChat:
    name = "mock"

    def __init__(self, model: str = "mock-extractive") -> None:
        self.model = model

    def _respond(self, messages: list[ChatMessage]) -> str:
        system = next((m.content for m in messages if m.role == "system"), "")
        user = next((m.content for m in reversed(messages) if m.role == "user"), "")
        task_match = TASK_HEADER.search(system)
        task = task_match.group(1) if task_match else "generate"
        if task == "route":
            return json.dumps(_route(user))
        if task == "refine":
            return _refine(user)
        if task == "summarize":
            return _summarize(user)
        if task == "direct":
            return (
                "Hello! I answer questions using your connected knowledge sources, with "
                "citations. Ask me about a document or topic."
            )
        return _generate(user)

    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        json_mode: bool = False,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> ChatResult:
        content = self._respond(messages)
        return ChatResult(
            content=content,
            provider=self.name,
            model=self.model,
            usage=Usage(
                prompt_tokens=sum(len(m.content.split()) for m in messages),
                completion_tokens=len(content.split()),
            ),
        )

    async def stream(
        self,
        messages: list[ChatMessage],
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> AsyncIterator[ChatStreamChunk]:
        result = await self.complete(messages)
        words = result.content.split(" ")
        for index, word in enumerate(words):
            yield ChatStreamChunk(delta=word if index == 0 else " " + word)
        yield ChatStreamChunk(result=result)
