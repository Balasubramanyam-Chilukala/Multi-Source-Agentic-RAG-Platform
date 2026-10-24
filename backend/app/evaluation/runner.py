"""Benchmark runner: identical corpus, cases, labels, and k for every retrieval mode.

1. ``prepare_corpus`` ingests the dataset documents into a dedicated collection of the tenant
   (reused across runs; re-embedded if the embedding model changed) and resolves each
   relevance label to concrete chunk ids. A label that matches no chunk aborts the run.
2. ``execute`` runs every case for each mode: a direct retrieval call for retrieval metrics,
   and optionally the full agent graph (in-memory checkpointer, no persistence) for answer
   metrics. Results go to ``evaluation_runs`` / ``evaluation_case_results``.
"""

import asyncio
import hashlib
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.graph import build_graph
from app.agent.state import AgentContext, AgentState
from app.core.config import Settings
from app.core.logging import get_logger
from app.db.models.evaluation import EvaluationCaseResult, EvaluationMode, EvaluationRun
from app.db.models.knowledge import (
    Chunk,
    Collection,
    Document,
    DocumentSource,
    DocumentStatus,
    EmbeddingModel,
    IngestionJob,
    JobStatus,
    SourceType,
)
from app.evaluation.dataset import Dataset, DatasetError, EvalCase, load_dataset
from app.evaluation.metrics import (
    answer_metrics,
    mean,
    normalize_answer,
    percentile,
    retrieval_metrics,
)
from app.ingestion.pipeline import IngestionPipeline
from app.providers.factory import ProviderRegistry
from app.retrieval.service import Retriever
from app.retrieval.types import RetrievalConfig

logger = get_logger("atlas.evaluation")

ALL_MODES: tuple[str, ...] = ("dense", "lexical", "hybrid", "hybrid_rerank")
MIN_SAMPLES_FOR_P95 = 20
_EXTENSION_MIME = {
    ".md": "text/markdown",
    ".txt": "text/plain",
    ".html": "text/html",
    ".csv": "text/csv",
    ".json": "application/json",
    ".pdf": "application/pdf",
}


@dataclass
class EvalOptions:
    dataset: str = "atlas-handbook-v1"
    modes: list[str] = field(default_factory=lambda: list(ALL_MODES))
    include_answers: bool = False
    answer_modes: list[str] | None = None  # default: all selected modes
    top_k: int = 5
    case_ids: list[str] | None = None
    pause_between_answers: float = 0.0  # throttle for free-tier token-per-minute limits


@dataclass
class Corpus:
    collection_id: uuid.UUID
    relevant: dict[str, set[str]]
    embedding_model: str


class EvaluationRunner:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        providers: ProviderRegistry,
        settings: Settings,
        pipeline: IngestionPipeline,
    ) -> None:
        self.sessionmaker = sessionmaker
        self.providers = providers
        self.settings = settings
        self.pipeline = pipeline

    # ------------------------------------------------------------------ corpus

    async def prepare_corpus(self, tenant_id: uuid.UUID, dataset: Dataset) -> Corpus:
        name = f"[eval] {dataset.name}@{dataset.version}"
        async with self.sessionmaker() as session, session.begin():
            collection = await session.scalar(
                select(Collection).where(Collection.tenant_id == tenant_id, Collection.name == name)
            )
            if collection is not None and collection.settings.get("managed") != "evaluation":
                raise DatasetError(
                    f"collection {name!r} exists but is not a managed evaluation corpus"
                )
            if collection is None:
                collection = Collection(
                    tenant_id=tenant_id,
                    name=name,
                    settings={"managed": "evaluation"},
                    description=f"Evaluation corpus for {dataset.name} (managed automatically)",
                )
                session.add(collection)
                await session.flush()
            collection_id = collection.id

        hash_to_file: dict[str, str] = {}
        for path in dataset.documents:
            content = path.read_bytes()
            digest = hashlib.sha256(content).hexdigest()
            hash_to_file[digest] = path.name
            await self._ensure_document(tenant_id, collection_id, path.name, content, digest)

        embedder = self.providers.embeddings()
        async with self.sessionmaker() as session:
            model = await session.scalar(
                select(EmbeddingModel).where(
                    EmbeddingModel.provider == embedder.name,
                    EmbeddingModel.model == embedder.model,
                    EmbeddingModel.version == embedder.version,
                )
            )
            # Stale = some chunk in the corpus was embedded with a different model.
            stale = (
                model is None
                or (
                    await session.scalar(
                        select(Chunk.id)
                        .where(
                            Chunk.collection_id == collection_id,
                            Chunk.embedding_model_id != model.id,
                        )
                        .limit(1)
                    )
                )
                is not None
            )
        if stale:
            logger.info("eval_corpus_reembed", collection_id=str(collection_id))
            await self.pipeline.reembed_collection(collection_id)

        async with self.sessionmaker() as session:
            documents = list(
                await session.scalars(
                    select(Document).where(Document.collection_id == collection_id)
                )
            )
            not_ready = [d.title for d in documents if d.status != DocumentStatus.READY]
            if not_ready:
                raise DatasetError(f"evaluation documents failed to ingest: {not_ready}")
            by_file = {
                hash_to_file[d.content_hash]: d.id
                for d in documents
                if d.content_hash in hash_to_file
            }
            rows = await session.execute(
                select(Chunk.id, Chunk.document_id, Chunk.text).where(
                    Chunk.collection_id == collection_id
                )
            )
            chunks = [(str(cid), did, normalize_answer(text)) for cid, did, text in rows]
            model_row = await session.scalar(
                select(EmbeddingModel).where(
                    EmbeddingModel.provider == embedder.name,
                    EmbeddingModel.model == embedder.model,
                    EmbeddingModel.version == embedder.version,
                )
            )

        relevant: dict[str, set[str]] = {}
        for case in dataset.cases:
            ids: set[str] = set()
            for label in case.relevant:
                needle = normalize_answer(label.contains)
                matched = {
                    cid
                    for cid, did, text in chunks
                    if did == by_file.get(label.doc) and needle in text
                }
                if not matched:
                    raise DatasetError(
                        f"case {case.id}: label {label.contains!r} matches no chunk in {label.doc}"
                    )
                ids |= matched
            relevant[case.id] = ids
        model_name = (
            f"{model_row.provider}/{model_row.model}@{model_row.version}"
            if model_row
            else "unknown"
        )
        return Corpus(collection_id, relevant, model_name)

    async def _ensure_document(
        self,
        tenant_id: uuid.UUID,
        collection_id: uuid.UUID,
        filename: str,
        content: bytes,
        digest: str,
    ) -> None:
        async with self.sessionmaker() as session, session.begin():
            document = await session.scalar(
                select(Document).where(
                    Document.collection_id == collection_id, Document.content_hash == digest
                )
            )
            if document is not None and document.status == DocumentStatus.READY:
                return
            if document is None:
                document = Document(
                    tenant_id=tenant_id,
                    collection_id=collection_id,
                    title=filename,
                    source_type=SourceType.UPLOAD,
                    mime_type=_EXTENSION_MIME.get("." + filename.rsplit(".", 1)[-1], "text/plain"),
                    size_bytes=len(content),
                    content_hash=digest,
                    status=DocumentStatus.PENDING,
                )
                session.add(document)
                await session.flush()
            document.status = DocumentStatus.PENDING  # the job only claims pending documents
            if await session.get(DocumentSource, document.id) is None:
                session.add(
                    DocumentSource(document_id=document.id, filename=filename, content=content)
                )
            job = IngestionJob(
                tenant_id=tenant_id, document_id=document.id, status=JobStatus.QUEUED
            )
            session.add(job)
            await session.flush()
            document_id, job_id = document.id, job.id
        await self.pipeline.process(document_id, job_id)

    # ------------------------------------------------------------------ runs

    def _config(
        self, mode: str, options: EvalOptions, corpus: Corpus | None, answers: bool
    ) -> dict[str, Any]:
        reranker = self.providers.reranker() if mode == "hybrid_rerank" else None
        chat = self.providers.chat() if answers else None
        return {
            "top_k": options.top_k,
            "candidate_k": max(self.settings.retrieval_candidate_k, options.top_k),
            "rrf_k": self.settings.rrf_k,
            "min_dense_similarity": self.settings.min_dense_similarity,
            "min_lexical_coverage": self.settings.min_lexical_coverage,
            "min_rerank_score": self.settings.min_rerank_score,
            "chunk_size": self.settings.chunk_size,
            "chunk_overlap": self.settings.chunk_overlap,
            "embedding_model": corpus.embedding_model if corpus else None,
            "reranker": f"{reranker.name}/{reranker.model}" if reranker else None,
            "include_answers": answers,
            "provider": chat.name if chat else None,
            "model": chat.model if chat else None,
            "max_retrieval_attempts": self.settings.agent_max_retrieval_attempts,
            "max_llm_calls": self.settings.agent_max_llm_calls,
        }

    async def create_runs(self, tenant_id: uuid.UUID, options: EvalOptions) -> list[uuid.UUID]:
        dataset = load_dataset(options.dataset)
        unknown = set(options.modes) - set(ALL_MODES)
        if unknown:
            raise DatasetError(f"unknown modes {sorted(unknown)}")
        if "hybrid_rerank" in options.modes and self.providers.reranker() is None:
            raise DatasetError("hybrid_rerank requires a configured reranker (RERANKER_PROVIDER)")
        ids = []
        async with self.sessionmaker() as session, session.begin():
            for mode in options.modes:
                answers = options.include_answers and (
                    options.answer_modes is None or mode in options.answer_modes
                )
                run = EvaluationRun(
                    tenant_id=tenant_id,
                    dataset_name=dataset.name,
                    dataset_version=dataset.version,
                    retrieval_mode=EvaluationMode(mode),
                    config=self._config(mode, options, None, answers),
                    status=JobStatus.QUEUED,
                )
                session.add(run)
                await session.flush()
                ids.append(run.id)
        return ids

    async def execute(
        self, tenant_id: uuid.UUID, run_ids: list[uuid.UUID], options: EvalOptions
    ) -> None:
        dataset = load_dataset(options.dataset)
        cases = [c for c in dataset.cases if not options.case_ids or c.id in options.case_ids]
        try:
            corpus = await self.prepare_corpus(tenant_id, dataset)
        except Exception as exc:
            logger.exception("eval_corpus_failed")
            await self._fail_all(run_ids, f"Corpus preparation failed: {exc}")
            return
        for run_id in run_ids:
            try:
                await self._execute_run(tenant_id, run_id, cases, corpus, options)
            except Exception as exc:
                logger.exception("eval_run_failed", run_id=str(run_id))
                await self._fail_all([run_id], f"{type(exc).__name__}: {exc}"[:1000])

    async def _fail_all(self, run_ids: list[uuid.UUID], message: str) -> None:
        async with self.sessionmaker() as session, session.begin():
            for run_id in run_ids:
                run = await session.get(EvaluationRun, run_id)
                if run is not None and run.status in (JobStatus.QUEUED, JobStatus.RUNNING):
                    run.status, run.error_message, run.finished_at = (
                        JobStatus.FAILED,
                        message,
                        datetime.now(UTC),
                    )

    async def _execute_run(
        self,
        tenant_id: uuid.UUID,
        run_id: uuid.UUID,
        cases: list[EvalCase],
        corpus: Corpus,
        options: EvalOptions,
    ) -> None:
        async with self.sessionmaker() as session, session.begin():
            run = await session.get(EvaluationRun, run_id)
            if run is None:
                return
            mode = run.retrieval_mode.value
            answers = bool(run.config.get("include_answers"))
            run.status, run.started_at = JobStatus.RUNNING, datetime.now(UTC)
            run.config = self._config(mode, options, corpus, answers)
        config = RetrievalConfig(
            mode=mode,  # type: ignore[arg-type]
            top_k=options.top_k,
            candidate_k=max(self.settings.retrieval_candidate_k, options.top_k),
            rrf_k=self.settings.rrf_k,
            min_dense_similarity=self.settings.min_dense_similarity,
            min_lexical_coverage=self.settings.min_lexical_coverage,
            min_rerank_score=self.settings.min_rerank_score,
        )
        retriever = Retriever(
            self.providers.embeddings(),
            self.providers.reranker() if mode == "hybrid_rerank" else None,
        )
        graph = (
            build_graph(
                InMemorySaver(), max_retrieval_attempts=self.settings.agent_max_retrieval_attempts
            )
            if answers
            else None
        )
        log = logger.bind(run_id=str(run_id), mode=mode)
        log.info("eval_run_started", cases=len(cases), answers=answers)

        results: list[dict[str, Any]] = []
        for case in cases:
            results.append(await self._run_case(tenant_id, case, corpus, config, retriever, graph))
            if answers and options.pause_between_answers:
                await asyncio.sleep(options.pause_between_answers)

        metrics = aggregate(results, cases, answers)
        async with self.sessionmaker() as session, session.begin():
            session.add_all(
                EvaluationCaseResult(
                    tenant_id=tenant_id,
                    evaluation_run_id=run_id,
                    case_id=r["case_id"],
                    retrieved_chunk_ids=r["retrieved"],
                    retrieval_metrics=r["retrieval_metrics"],
                    answer_metrics=r["answer_metrics"],
                    answer=r["answer"],
                    latency_ms=r["latency_ms"],
                    error_message=r["error"],
                )
                for r in results
            )
            run = await session.get(EvaluationRun, run_id)
            if run is not None:
                run.metrics, run.status, run.finished_at = (
                    metrics,
                    JobStatus.SUCCEEDED,
                    datetime.now(UTC),
                )
        log.info(
            "eval_run_finished",
            recall=metrics["retrieval"]["recall@k"],
            mrr=metrics["retrieval"]["mrr"],
        )

    async def _run_case(
        self,
        tenant_id: uuid.UUID,
        case: EvalCase,
        corpus: Corpus,
        config: RetrievalConfig,
        retriever: Retriever,
        graph: Any,
    ) -> dict[str, Any]:
        relevant = corpus.relevant[case.id]
        result: dict[str, Any] = {
            "case_id": case.id,
            "category": case.category,
            "retrieved": [],
            "retrieval_metrics": {},
            "answer_metrics": {},
            "answer": None,
            "latency_ms": None,
            "retrieval_latency_ms": None,
            "error": None,
        }
        try:
            started = time.perf_counter()
            async with self.sessionmaker() as session:
                retrieval = await retriever.retrieve(
                    session,
                    tenant_id=tenant_id,
                    collection_ids=[corpus.collection_id],
                    query=case.question,
                    config=config,
                )
            result["retrieval_latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
            retrieved = [str(c.chunk_id) for c in retrieval.chunks]
            result["retrieved"] = retrieved
            result["retrieval_metrics"] = {
                **retrieval_metrics(retrieved, relevant, config.top_k),
                "sufficient": retrieval.sufficiency.sufficient,
            }
            result["latency_ms"] = result["retrieval_latency_ms"]
            if graph is not None:
                started = time.perf_counter()
                state = await self._answer(graph, tenant_id, case, corpus, config)
                result["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
                result["answer"] = state.get("answer", "")
                if state.get("outcome") == "error":
                    result["error"] = (state.get("error") or {}).get("category", "error")
                result["answer_metrics"] = {
                    **answer_metrics(
                        answer=state.get("answer", ""),
                        outcome=state.get("outcome") or "error",
                        answerable=case.answerable,
                        expected_outcome=case.expected_outcome,
                        required_facts=case.required_facts,
                        forbidden_facts=case.forbidden_facts,
                        cited_chunk_ids=[c["chunk_id"] for c in state.get("citations", [])],
                        relevant=relevant,
                        claims=state.get("claims", []),
                    ),
                    "outcome": state.get("outcome"),
                    "llm_calls": state.get("llm_calls", 0),
                    "prompt_tokens": state.get("prompt_tokens", 0),
                    "completion_tokens": state.get("completion_tokens", 0),
                }
        except Exception as exc:
            logger.warning("eval_case_failed", case_id=case.id, error_type=type(exc).__name__)
            result["error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
        return result

    async def _answer(
        self,
        graph: Any,
        tenant_id: uuid.UUID,
        case: EvalCase,
        corpus: Corpus,
        config: RetrievalConfig,
    ) -> dict[str, Any]:
        sessionmaker = self.sessionmaker

        @asynccontextmanager
        async def retrieve_session() -> AsyncIterator[AsyncSession]:
            async with sessionmaker() as session:
                yield session

        async def list_documents() -> list[dict[str, Any]]:
            async with sessionmaker() as session:
                rows = await session.execute(
                    select(Document.title, Document.status, Document.chunk_count).where(
                        Document.collection_id == corpus.collection_id
                    )
                )
                return [{"title": t, "status": s.value, "chunk_count": n} for t, s, n in rows]

        async def find_document_chunks(title: str) -> tuple[str | None, list[Any]]:
            return None, []

        async def persist(state: AgentState) -> dict[str, str]:
            return {"run_id": "", "message_id": ""}  # evaluation runs are not stored as chats

        context = AgentContext(
            tenant_id=tenant_id,
            chat=self.providers.chat(),
            retriever=Retriever(
                self.providers.embeddings(),
                self.providers.reranker() if config.mode == "hybrid_rerank" else None,
            ),
            retrieval_config=config,
            max_retrieval_attempts=self.settings.agent_max_retrieval_attempts,
            max_llm_calls=self.settings.agent_max_llm_calls,
            history_turns=0,
            max_question_chars=self.settings.max_question_chars,
            retrieve_session=retrieve_session,
            list_documents=list_documents,
            find_document_chunks=find_document_chunks,
            persist=persist,
        )
        final: dict[str, Any] = {}
        async for mode, payload in graph.astream(
            {"question": case.question, "collection_ids": [str(corpus.collection_id)]},
            {"configurable": {"thread_id": f"eval-{uuid.uuid4()}"}, "recursion_limit": 50},
            context=context,
            stream_mode=["values"],
        ):
            if mode == "values" and isinstance(payload, dict):
                final = payload
        return final


def aggregate(
    results: list[dict[str, Any]], cases: list[EvalCase], answers: bool
) -> dict[str, Any]:
    by_case = {c.id: c for c in cases}
    keys = ("recall@k", "precision@k", "hit_rate@k", "mrr", "ndcg@k")
    retrieval = {k: _round(mean([r["retrieval_metrics"].get(k) for r in results])) for k in keys}
    retrieval_latencies = [
        r["retrieval_latency_ms"] for r in results if r["retrieval_latency_ms"] is not None
    ]
    latencies = [r["latency_ms"] for r in results if r["latency_ms"] is not None]
    enough = len(retrieval_latencies) >= MIN_SAMPLES_FOR_P95
    metrics: dict[str, Any] = {
        "case_count": len(results),
        "labelled_case_count": sum(
            1 for r in results if r["retrieval_metrics"].get("recall@k") is not None
        ),
        "retrieval": retrieval,
        "latency_ms": {
            "retrieval_p50": _round(percentile(retrieval_latencies, 50), 1),
            "retrieval_p95": _round(percentile(retrieval_latencies, 95), 1) if enough else None,
        },
        "error_rate": _round(sum(1 for r in results if r["error"]) / len(results))
        if results
        else None,
    }
    if answers:
        answer_keys = (
            "correctness",
            "completeness",
            "citation_precision",
            "faithfulness",
            "abstention_accuracy",
            "forbidden_content",
        )
        metrics["answer"] = {
            k: _round(mean([r["answer_metrics"].get(k) for r in results])) for k in answer_keys
        }
        metrics["answer"]["judge"] = "deterministic reference checks (no LLM judge)"
        metrics["latency_ms"]["end_to_end_p50"] = _round(percentile(latencies, 50), 1)
        metrics["latency_ms"]["end_to_end_p95"] = (
            _round(percentile(latencies, 95), 1) if len(latencies) >= MIN_SAMPLES_FOR_P95 else None
        )
        metrics["tokens"] = {
            "prompt": sum(r["answer_metrics"].get("prompt_tokens") or 0 for r in results),
            "completion": sum(r["answer_metrics"].get("completion_tokens") or 0 for r in results),
            "llm_calls": sum(r["answer_metrics"].get("llm_calls") or 0 for r in results),
        }
    categories: dict[str, dict[str, Any]] = {}
    for r in results:
        category = by_case[r["case_id"]].category
        bucket = categories.setdefault(
            category, {"count": 0, "_recall": [], "_mrr": [], "_correct": [], "_abstain": []}
        )
        bucket["count"] += 1
        bucket["_recall"].append(r["retrieval_metrics"].get("recall@k"))
        bucket["_mrr"].append(r["retrieval_metrics"].get("mrr"))
        bucket["_correct"].append(r["answer_metrics"].get("correctness"))
        bucket["_abstain"].append(r["answer_metrics"].get("abstention_accuracy"))
    metrics["by_category"] = {
        name: {
            "count": b["count"],
            "recall@k": _round(mean(b["_recall"])),
            "mrr": _round(mean(b["_mrr"])),
            **(
                {
                    "correctness": _round(mean(b["_correct"])),
                    "abstention_accuracy": _round(mean(b["_abstain"])),
                }
                if answers
                else {}
            ),
        }
        for name, b in sorted(categories.items())
    }
    return metrics


def _round(value: float | None, digits: int = 4) -> float | None:
    return round(value, digits) if value is not None else None
