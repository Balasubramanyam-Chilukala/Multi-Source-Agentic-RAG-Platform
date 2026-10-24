"""Routing decisions.

Unambiguous cases are decided by rules, so they cost no LLM call (important on free tiers
with tight token-per-minute limits). Follow-up questions — where pronouns must be resolved
against the conversation — go to the LLM router, whose JSON output is validated by Pydantic.
"""

import json
import re

from pydantic import BaseModel, Field, ValidationError

from app.agent.state import Action
from app.core.text import STOPWORDS, tokenize

ARITHMETIC = re.compile(
    r"^\s*(?:what\s+is\s+|calculate\s+|compute\s+)?([\d\s.,+\-*/%()^×÷]+)\??\s*$", re.IGNORECASE
)
GREETING = re.compile(
    r"^\s*(hi|hello|hey|good (morning|afternoon|evening)|thanks|thank you|cheers)\b"
    r"[\s!.,]*\w{0,12}[\s!.]*$",
    re.IGNORECASE,
)
LIST_DOCUMENTS = re.compile(
    r"\b(what|which|list|show)\b.{0,40}\b(documents?|files?|sources?)\b.{0,40}\b(have|available|uploaded|connected|indexed)\b"
    r"|^\s*list (my |all |the )?(documents|files|sources)\b",
    re.IGNORECASE,
)
SUMMARIZE = re.compile(
    r"^\s*(?:please\s+)?summari[sz]e\s+(?:the\s+)?(?:document\s+)?[\"'“]?(.+?)[\"'”]?\s*\.?\s*$",
    re.IGNORECASE,
)
# Back-references that need conversation context. Existential "there" ("are there any…") is not one.
PRONOUNS = {"it", "that", "this", "they", "those", "these", "them", "he", "she"}


class RouteDecision(BaseModel):
    action: Action
    search_query: str = Field(default="", max_length=500)
    clarification_question: str = Field(default="", max_length=500)
    expression: str = Field(default="", max_length=200)
    document_title: str = Field(default="", max_length=300)
    source: str = "rules"


def rule_based_route(question: str, *, has_history: bool) -> RouteDecision | None:
    """Return a decision for clear-cut messages, or ``None`` to defer to the LLM router."""
    arithmetic = ARITHMETIC.match(question)
    if (
        arithmetic
        and any(ch.isdigit() for ch in arithmetic.group(1))
        and re.search(r"[+\-*/%^×÷]", arithmetic.group(1))
    ):
        return RouteDecision(action="calculate", expression=arithmetic.group(1).strip())
    if GREETING.match(question):
        return RouteDecision(action="direct")
    if LIST_DOCUMENTS.search(question):
        return RouteDecision(action="list_documents")
    summarize = SUMMARIZE.match(question)
    if summarize and len(summarize.group(1)) >= 3:
        return RouteDecision(action="summarize_document", document_title=summarize.group(1).strip())

    words = tokenize(question)
    content = [w for w in words if w not in STOPWORDS]
    refers_back = bool(set(words) & PRONOUNS) and len(content) <= 2
    if has_history and (refers_back or len(words) <= 6):
        return None  # likely a follow-up: let the LLM resolve it against the history
    if not content or (refers_back and not has_history):
        return RouteDecision(
            action="clarify",
            clarification_question=(
                "Could you add a little more detail about what you are looking for — for "
                "example the topic, policy, or document name?"
            ),
        )
    return RouteDecision(action="retrieve", search_query=question)


def parse_llm_route(raw: str, question: str) -> RouteDecision:
    """Validate the router's JSON. Anything malformed falls back to a plain retrieval."""
    try:
        data = json.loads(raw)
        decision = RouteDecision.model_validate({**data, "source": "llm"})
    except (ValueError, ValidationError, TypeError):
        return RouteDecision(action="retrieve", search_query=question, source="llm_fallback")
    if decision.action == "retrieve" and not decision.search_query.strip():
        decision.search_query = question
    if decision.action == "clarify" and not decision.clarification_question.strip():
        decision.clarification_question = "Could you clarify what you would like to know?"
    return decision


class RefinedQuery(BaseModel):
    search_query: str = Field(min_length=1, max_length=500)


def parse_refined_query(raw: str) -> str:
    """The refine step's JSON output, or an empty string if it is malformed."""
    try:
        return RefinedQuery.model_validate(json.loads(raw)).search_query.strip()
    except (ValueError, ValidationError, TypeError):
        return ""
