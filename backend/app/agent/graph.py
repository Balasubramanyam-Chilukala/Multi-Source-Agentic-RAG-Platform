"""The Atlas agent as an explicit LangGraph state machine.

    START → validate_input → route
    route → retrieve → (sufficient?) generate → validate_answer → finalize
                     ↘ refine → retrieve   (bounded by max_retrieval_attempts)
                     ↘ abstain              (attempts exhausted)
    validate_answer → generate (one citation retry) | abstain
    route → summarize → validate_answer
    route → calculate | list_documents | direct | clarify → finalize
    finalize → persist → END

Budgets: at most ``max_retrieval_attempts`` retrievals and ``max_llm_calls`` LLM calls per run.
Every node is traced (latency, status) and every provider failure is converted into a
categorized error that still flows through ``finalize`` and ``persist``.
"""

import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime

from app.agent import prompts
from app.agent.citations import validate_citations
from app.agent.routing import RouteDecision, parse_llm_route, parse_refined_query, rule_based_route
from app.agent.state import AgentContext, AgentState, Turn
from app.agent.tools import CalculationError, calculate, looks_like_injection
from app.core.logging import get_logger
from app.core.observability import tracer
from app.core.text import STOPWORDS, tokenize
from app.providers.base import ChatMessage, ChatResult, ProviderError
from app.retrieval.types import EvidenceChunk

logger = get_logger("atlas.agent")

Node = Callable[[AgentState, Runtime[AgentContext]], Awaitable[dict[str, Any]]]
MIN_SUPPORTED_RATIO = 0.5
GENERIC_ERROR = "Something went wrong while answering. Please try again."


def _emit(runtime: Runtime[AgentContext], event: dict[str, Any]) -> None:
    runtime.stream_writer(event)


def traced(name: str) -> Callable[[Node], Node]:
    """Time the node, record it in the run trace, and turn failures into categorized errors."""

    def decorator(fn: Node) -> Node:
        async def wrapper(state: AgentState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
            _emit(runtime, {"type": "step", "node": name, "status": "started"})
            started = time.perf_counter()
            status, detail = "ok", None
            span = tracer.start_span(f"agent.{name}")  # no-op unless OpenTelemetry is enabled
            try:
                update = await fn(state, runtime)
            except ProviderError as exc:
                status, detail = "error", exc.category
                logger.warning(
                    "agent_provider_error", node=name, category=exc.category, provider=exc.provider
                )
                update = {
                    "outcome": "error",
                    "error": {"category": exc.category, "message": exc.user_message, "node": name},
                }
            except Exception as exc:
                status, detail = "error", "internal_error"
                logger.exception("agent_node_failed", node=name, error_type=type(exc).__name__)
                update = {
                    "outcome": "error",
                    "error": {"category": "internal_error", "message": GENERIC_ERROR, "node": name},
                }
            span.set_attribute("atlas.node.status", status)
            if detail:
                span.set_attribute("atlas.error_category", detail)
            span.end()
            info = update.pop("_trace", {})
            entry = {
                "node": name,
                "status": status,
                "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                **info,
            }
            if detail:
                entry["error_category"] = detail
            base = update.get("trace", state.get("trace", []))
            update["trace"] = [*base, entry]
            _emit(
                runtime,
                {"type": "step", "node": name, "status": status, "latency_ms": entry["latency_ms"]},
            )
            return update

        wrapper.__name__ = fn.__name__
        return wrapper

    return decorator


def _usage(state: AgentState, result: ChatResult) -> dict[str, Any]:
    return {
        "llm_calls": state.get("llm_calls", 0) + 1,
        "prompt_tokens": state.get("prompt_tokens", 0) + (result.usage.prompt_tokens or 0),
        "completion_tokens": state.get("completion_tokens", 0)
        + (result.usage.completion_tokens or 0),
        "provider": result.provider,
        "model": result.model,
        # Providers a fallback chain skipped before this call was answered.
        "provider_failovers": [*state.get("provider_failovers", []), *result.failovers],
    }


def _evidence(state: AgentState) -> list[EvidenceChunk]:
    chunks = []
    for item in state.get("evidence", []):
        data = dict(item)
        for key in ("chunk_id", "document_id", "collection_id"):
            data[key] = uuid.UUID(str(data[key]))
        data.pop("suspicious", None)
        chunks.append(EvidenceChunk(**data))
    return chunks


def _history(state: AgentState, runtime: Runtime[AgentContext]) -> list[Turn]:
    limit = runtime.context.history_turns * 2
    return state.get("turns", [])[-limit:] if limit else []


# --------------------------------------------------------------------------- nodes


@traced("validate_input")
async def validate_input(state: AgentState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    question = " ".join((state.get("question") or "").split())
    reset: dict[str, Any] = {
        "question": question,
        "trace": [],
        "retrieval_attempts": 0,
        "llm_calls": 0,
        "evidence": [],
        "sufficiency": {},
        "retrieval_log": [],
        "suspicious_chunk_ids": [],
        "draft": "",
        "citation_retry": False,
        "answer": "",
        "citations": [],
        "claims": [],
        "outcome": None,
        "error": None,
        "search_query": "",
        "clarification_question": "",
        "expression": "",
        "document_title": "",
        "action": "retrieve",
        "route_source": "",
        "next_step": "",
        "provider": runtime.context.chat.name,
        "model": runtime.context.chat.model,
        "prompt_tokens": 0,
        "provider_failovers": [],
        "completion_tokens": 0,
        "run_id": "",
        "assistant_message_id": "",
    }
    if not question:
        reset.update(outcome="clarification", answer="Please enter a question.")
    elif len(question) > runtime.context.max_question_chars:
        reset.update(
            outcome="error",
            error={
                "category": "validation",
                "node": "validate_input",
                "message": (
                    f"Questions are limited to {runtime.context.max_question_chars} characters."
                ),
            },
        )
    return reset


@traced("route")
async def route(state: AgentState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    history = _history(state, runtime)
    decision = rule_based_route(state["question"], has_history=bool(history))
    update: dict[str, Any] = {}
    if decision is None and state.get("llm_calls", 0) + 2 > runtime.context.max_llm_calls:
        # Not enough budget for routing *and* answering: answering wins.
        decision = RouteDecision(action="retrieve", search_query=state["question"], source="budget")
    if decision is None:
        try:
            result = await runtime.context.chat.complete(
                [
                    ChatMessage("system", prompts.ROUTE_SYSTEM),
                    ChatMessage("user", prompts.route_user_message(state["question"], history)),
                ],
                json_mode=True,
                max_tokens=300,
                temperature=0,
            )
            update.update(_usage(state, result))
            decision = parse_llm_route(result.content, state["question"])
        except ProviderError as exc:
            # Degrade gracefully: search with the raw question rather than failing the run.
            logger.warning("router_degraded", category=exc.category)
            decision = RouteDecision(
                action="retrieve", search_query=state["question"], source="degraded"
            )
    update.update(
        action=decision.action,
        search_query=decision.search_query or state["question"],
        clarification_question=decision.clarification_question,
        expression=decision.expression,
        document_title=decision.document_title,
        route_source=decision.source,
        _trace={"action": decision.action, "decided_by": decision.source},
    )
    return update


@traced("retrieve")
async def retrieve(state: AgentState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    ctx = runtime.context
    attempt = state.get("retrieval_attempts", 0) + 1
    async with ctx.retrieve_session() as session:
        result = await ctx.retriever.retrieve(
            session,
            tenant_id=ctx.tenant_id,
            collection_ids=[uuid.UUID(c) for c in state.get("collection_ids", [])],
            query=state["search_query"],
            question=state["question"],
            config=ctx.retrieval_config,
        )
    suspicious = [str(c.chunk_id) for c in result.chunks if looks_like_injection(c.text)]
    evidence = [{**c.to_dict(), "suspicious": str(c.chunk_id) in suspicious} for c in result.chunks]
    log_entry = {
        "attempt": attempt,
        "query": result.query,
        "mode": result.config.mode,
        "results": len(result.chunks),
        "chunk_ids": [str(c.chunk_id) for c in result.chunks],
        "scores": [
            {
                "chunk_id": str(c.chunk_id),
                "dense": c.dense_score,
                "lexical": c.lexical_score,
                "fused": c.fused_score,
                "rerank": c.rerank_score,
            }
            for c in result.chunks
        ],
        "sufficiency": vars(result.sufficiency),
        "timings_ms": result.timings_ms,
        "warnings": result.warnings,
        "embedding_model": result.embedding_model,
        "reranker": result.reranker,
    }
    return {
        "retrieval_attempts": attempt,
        "evidence": evidence,
        "sufficiency": vars(result.sufficiency),
        "suspicious_chunk_ids": suspicious,
        "retrieval_log": [*state.get("retrieval_log", []), log_entry],
        "_trace": {
            "attempt": attempt,
            "results": len(result.chunks),
            "sufficient": result.sufficiency.sufficient,
        },
    }


@traced("refine")
async def refine(state: AgentState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    ctx = runtime.context
    previous = state["search_query"]
    keyword_query = " ".join(w for w in tokenize(state["question"]) if w not in STOPWORDS)
    # Keep at least one LLM call in reserve for generating the answer.
    if state.get("llm_calls", 0) + 2 <= ctx.max_llm_calls:
        try:
            result = await ctx.chat.complete(
                [
                    ChatMessage("system", prompts.REFINE_SYSTEM),
                    ChatMessage("user", prompts.refine_user_message(state["question"], previous)),
                ],
                json_mode=True,
                max_tokens=120,
                temperature=0,
            )
            update = _usage(state, result)
            refined = parse_refined_query(result.content)
        except ProviderError as exc:
            logger.warning("refine_degraded", category=exc.category)
            update, refined = {}, keyword_query
    else:
        update, refined = {}, keyword_query
    if not refined or refined.strip().lower() == previous.strip().lower():
        refined = (
            keyword_query
            if keyword_query.lower() != previous.lower()
            else f"{keyword_query} policy guidelines"
        )
    return {**update, "search_query": refined, "_trace": {"query": refined}}


async def _stream_answer(
    state: AgentState, runtime: Runtime[AgentContext], messages: list[ChatMessage]
) -> dict[str, Any]:
    if state.get("citation_retry"):
        _emit(runtime, {"type": "token_reset"})
    result: ChatResult | None = None
    async for chunk in runtime.context.chat.stream(messages):
        if chunk.delta:
            _emit(runtime, {"type": "token", "text": chunk.delta})
        if chunk.result is not None:
            result = chunk.result
    if result is None:
        raise ProviderError("stream ended without a result", provider=runtime.context.chat.name)
    return {**_usage(state, result), "draft": result.content}


def _budget_spent(state: AgentState, runtime: Runtime[AgentContext]) -> bool:
    return state.get("llm_calls", 0) >= runtime.context.max_llm_calls


def _retry_messages(state: AgentState) -> list[ChatMessage]:
    if not state.get("citation_retry"):
        return []
    return [
        ChatMessage("assistant", state.get("draft", "")),
        ChatMessage("user", prompts.STRICT_CITATION_REMINDER),
    ]


@traced("generate")
async def generate(state: AgentState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    if _budget_spent(state, runtime):
        return {"draft": "", "_trace": {"skipped": "llm_budget_exhausted"}}
    evidence = _evidence(state)
    sources = prompts.format_sources(evidence, set(state.get("suspicious_chunk_ids", [])))
    messages = [
        ChatMessage("system", prompts.GENERATE_SYSTEM),
        ChatMessage(
            "user",
            prompts.generate_user_message(state["question"], _history(state, runtime), sources),
        ),
        *_retry_messages(state),
    ]
    update = await _stream_answer(state, runtime, messages)
    update["_trace"] = {"sources": len(evidence), "retry": bool(state.get("citation_retry"))}
    return update


@traced("summarize")
async def summarize(state: AgentState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    if _budget_spent(state, runtime):
        return {"draft": "", "_trace": {"skipped": "llm_budget_exhausted"}}
    title, chunks = await runtime.context.find_document_chunks(state.get("document_title", ""))
    if title is None:
        documents = await runtime.context.list_documents()
        names = ", ".join(f"“{d['title']}”" for d in documents[:8]) or "none yet"
        return {
            "outcome": "clarification",
            "answer": (
                f"I couldn't find a document matching “{state.get('document_title', '')}”. "
                f"Available documents: {names}."
            ),
        }
    suspicious = [str(c.chunk_id) for c in chunks if looks_like_injection(c.text)]
    evidence = [{**c.to_dict(), "suspicious": str(c.chunk_id) in suspicious} for c in chunks]
    sources = prompts.format_sources(chunks, set(suspicious))
    update = await _stream_answer(
        state,
        runtime,
        [
            ChatMessage("system", prompts.SUMMARIZE_SYSTEM),
            ChatMessage("user", f"Document: {title}\n\nSources:\n{sources}"),
            *_retry_messages(state),
        ],
    )
    update.update(
        evidence=evidence,
        suspicious_chunk_ids=suspicious,
        _trace={"document": title, "sources": len(chunks)},
    )
    return update


@traced("validate_answer")
async def validate_answer(state: AgentState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    draft = state.get("draft", "").strip()
    if not draft or prompts.INSUFFICIENT_SENTINEL in draft[:80].upper().replace(" ", "_"):
        return {
            "_trace": {"verdict": "model_abstained" if draft else "no_draft"},
            "citation_retry": False,
            "next_step": "abstain",
        }
    validated = validate_citations(draft, _evidence(state))
    info = {
        "valid_citations": len(validated.citations),
        "invalid_markers": validated.invalid_markers,
        "removed_urls": len(validated.removed_urls),
        "supported_ratio": round(validated.supported_ratio, 3),
    }
    if not validated.citations or validated.supported_ratio < MIN_SUPPORTED_RATIO:
        can_retry = (
            not state.get("citation_retry")
            and state.get("llm_calls", 0) < runtime.context.max_llm_calls
        )
        verdict = "retry" if can_retry else "unsupported"
        return {
            "_trace": {**info, "verdict": verdict},
            "citation_retry": can_retry,
            "next_step": "generate" if can_retry else "abstain",
        }
    return {
        "answer": validated.text,
        "citations": [c.to_dict() for c in validated.citations],
        "claims": validated.claims,
        "outcome": "answered",
        "next_step": "finalize",
        "_trace": {**info, "verdict": "accepted"},
    }


@traced("abstain")
async def abstain(state: AgentState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    return {
        "outcome": "abstained",
        "answer": prompts.ABSTAIN_MESSAGE,
        "citations": [],
        "claims": [],
        "_trace": {"reason": state.get("sufficiency", {}).get("reason", "unsupported_answer")},
    }


@traced("calculate")
async def calculate_node(state: AgentState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    try:
        value = calculate(state.get("expression", ""))
    except CalculationError as exc:
        return {
            "outcome": "clarification",
            "answer": f"I couldn't evaluate that expression: {exc}.",
        }
    return {
        "outcome": "answered",
        "answer": f"`{state['expression']}` = **{value}**",
        "_trace": {"tool": "calculator"},
    }


@traced("list_documents")
async def list_documents(state: AgentState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    documents = await runtime.context.list_documents()
    if not documents:
        return {
            "outcome": "answered",
            "answer": "No documents have been added to the selected collections yet.",
        }
    lines = [
        f"- **{d['title']}** — {d['status']}, {d['chunk_count']} passages" for d in documents[:50]
    ]
    more = f"\n\n…and {len(documents) - 50} more." if len(documents) > 50 else ""
    return {
        "outcome": "answered",
        "answer": "Documents in the selected collections:\n\n" + "\n".join(lines) + more,
        "_trace": {"tool": "document_metadata", "count": len(documents)},
    }


@traced("direct")
async def direct(state: AgentState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    ctx = runtime.context
    # Rule-routed greetings get a fixed reply; only LLM-routed messages spend another call.
    if state.get("llm_calls", 0) >= ctx.max_llm_calls or state.get("route_source") == "rules":
        answer = (
            "Hello! I answer questions from your connected knowledge sources and cite the passages "
            "I used. Ask me about a policy, process, or document."
        )
        return {"outcome": "answered", "answer": answer}
    result = await ctx.chat.complete(
        [ChatMessage("system", prompts.DIRECT_SYSTEM), ChatMessage("user", state["question"])],
        max_tokens=200,
    )
    return {**_usage(state, result), "outcome": "answered", "answer": result.content.strip()}


@traced("clarify")
async def clarify(state: AgentState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    question = (
        state.get("clarification_question") or "Could you clarify what you would like to know?"
    )
    return {"outcome": "clarification", "answer": question}


@traced("finalize")
async def finalize(state: AgentState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    outcome = state.get("outcome") or "error"
    answer = state.get("answer", "")
    if outcome == "error":
        answer = (state.get("error") or {}).get("message", GENERIC_ERROR)
    turns: list[Turn] = (
        [
            {"role": "user", "content": state.get("question", "")},
            {"role": "assistant", "content": answer},
        ]
        if state.get("question")
        else []
    )
    return {"outcome": outcome, "answer": answer, "turns": turns}


@traced("persist")
async def persist(state: AgentState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    ids = await runtime.context.persist(state)
    return {"run_id": ids["run_id"], "assistant_message_id": ids["message_id"]}


# --------------------------------------------------------------------------- routing


def after_validate(state: AgentState) -> str:
    return "finalize" if state.get("outcome") else "route"


def after_route(state: AgentState) -> str:
    if state.get("outcome") == "error":
        return "finalize"
    return {
        "calculate": "calculate",
        "list_documents": "list_documents",
        "summarize_document": "summarize",
        "direct": "direct",
        "clarify": "clarify",
    }.get(state.get("action", "retrieve"), "retrieve")


def make_after_retrieve(max_attempts: int) -> Callable[[AgentState], str]:
    def after_retrieve(state: AgentState) -> str:
        if state.get("outcome") == "error":
            return "finalize"
        if state.get("sufficiency", {}).get("sufficient"):
            return "generate"
        if state.get("retrieval_attempts", 0) < max_attempts:
            return "refine"
        return "abstain"

    return after_retrieve


def after_generation(state: AgentState) -> str:
    return "finalize" if state.get("outcome") else "validate_answer"


def after_validate_answer(state: AgentState) -> str:
    if state.get("outcome"):
        return "finalize"
    if state.get("next_step") != "generate":
        return "abstain"
    # Retry with the step that produced the draft (a summary must not become a RAG answer).
    return "summarize" if state.get("action") == "summarize_document" else "generate"


def build_graph(
    checkpointer: BaseCheckpointSaver[Any] | None, *, max_retrieval_attempts: int
) -> CompiledStateGraph[AgentState, AgentContext, AgentState, AgentState]:
    graph = StateGraph(AgentState, context_schema=AgentContext)
    nodes: dict[str, Node] = {
        "validate_input": validate_input,
        "route": route,
        "retrieve": retrieve,
        "refine": refine,
        "generate": generate,
        "summarize": summarize,
        "validate_answer": validate_answer,
        "abstain": abstain,
        "calculate": calculate_node,
        "list_documents": list_documents,
        "direct": direct,
        "clarify": clarify,
        "finalize": finalize,
        "persist": persist,
    }
    for name, node in nodes.items():
        graph.add_node(name, node)  # type: ignore[call-overload]

    graph.add_edge(START, "validate_input")
    graph.add_conditional_edges("validate_input", after_validate, ["route", "finalize"])
    graph.add_conditional_edges(
        "route",
        after_route,
        ["retrieve", "calculate", "list_documents", "summarize", "direct", "clarify", "finalize"],
    )
    graph.add_conditional_edges(
        "retrieve",
        make_after_retrieve(max_retrieval_attempts),
        ["generate", "refine", "abstain", "finalize"],
    )
    graph.add_edge("refine", "retrieve")
    graph.add_conditional_edges("generate", after_generation, ["validate_answer", "finalize"])
    graph.add_conditional_edges("summarize", after_generation, ["validate_answer", "finalize"])
    graph.add_conditional_edges(
        "validate_answer",
        after_validate_answer,
        ["generate", "summarize", "abstain", "finalize"],
    )
    for terminal in ("abstain", "calculate", "list_documents", "direct", "clarify"):
        graph.add_edge(terminal, "finalize")
    graph.add_edge("finalize", "persist")
    graph.add_edge("persist", END)
    return graph.compile(checkpointer=checkpointer, name="atlas-agent")
