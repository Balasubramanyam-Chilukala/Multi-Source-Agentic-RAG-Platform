"""Benchmark runner: identical corpus, cases, labels, and k for every retrieval mode.

1. ``prepare_corpus`` ingests the dataset into a managed collection of the tenant (reused across
   runs; rebuilt if the embedding model changed) and resolves relevance labels to chunk ids.
   When every label names a whole document (BEIR), retrieval is scored at document level: chunks
   are mapped to their document, the ranking is de-duplicated (a document ranks at its best
   chunk) and the top k *documents* are compared with the qrels, as in the BEIR protocol.
   Passage-level labels (the handbook) are scored per chunk.
   ``files`` datasets go through the normal ingestion pipeline; large ``beir`` corpora use a
   bulk path that batches embeddings across documents.
2. ``execute`` runs the requested split (``test`` by default; ``dev`` is for tuning) once per
   mode per repetition. Query embeddings are recomputed in every repetition, so run-to-run
   stability of retrieval is measured, not assumed. With answers enabled, the full agent graph
   runs (in-memory checkpointer, no persistence) and, optionally, a faithfulness judge scores
   the cited sentences. Results go to ``evaluation_runs`` / ``evaluation_case_results``.
"""

import asyncio
import hashlib
import random
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import delete, func, select
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
from app.evaluation.dataset import Dataset, DatasetError, DocRecord, EvalCase, load_dataset
from app.evaluation.judge import FaithfulnessJudge, claims_from_answer
from app.evaluation.metrics import (
    answer_metrics,
    mean,
    normalize_answer,
    percentile,
    retrieval_metrics,
)
from app.ingestion.chunker import chunk_sections
from app.ingestion.parsers import Section
from app.ingestion.pipeline import IngestionPipeline, embedding_text
from app.providers.factory import ProviderRegistry
from app.retrieval.service import Retriever
from app.retrieval.types import RetrievalConfig
from app.services.embedding_registry import ensure_embedding_model

logger = get_logger("atlas.evaluation")

ALL_MODES: tuple[str, ...] = ("dense", "lexical", "hybrid", "hybrid_rerank")
DENSE_MODES = ("dense", "hybrid", "hybrid_rerank")
MIN_SAMPLES_FOR_P95 = 20
BULK_DOCS_PER_TRANSACTION = 250
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
    split: str = "test"
    runs: int = 1
    include_answers: bool = False
    answer_modes: list[str] | None = None  # default: all selected modes
    answer_sample: int | None = None  # answer a fixed random subset (quota); None = all cases
    sample_seed: int = 13
    top_k: int = 5
    case_ids: list[str] | None = None
    pause_between_answers: float = 0.0  # throttle for free-tier token-per-minute limits


@dataclass
class Corpus:
    collection_id: uuid.UUID
    relevant: dict[str, set[str]]
    embedding_model: str
    # Set when every label is a whole document: scoring then happens over de-duplicated documents.
    relevant_docs: dict[str, set[str]] | None = None
    chunk_document: dict[str, str] = field(default_factory=dict)

    @property
    def unit(self) -> str:
        return "document" if self.relevant_docs is not None else "chunk"


DOCUMENT_POOL_FACTOR = 4


def document_pool(config: RetrievalConfig) -> RetrievalConfig:
    """Document-level scoring needs enough chunks to fill k distinct documents (abstracts are
    split into ~2 chunks), so every mode ranks a pool of 4 x k chunks before de-duplication."""
    pool = max(config.candidate_k, DOCUMENT_POOL_FACTOR * config.top_k)
    return replace(config, top_k=pool, candidate_k=pool)


def rank_documents(chunk_ids: list[str], chunk_document: dict[str, str]) -> list[str]:
    """Collapse a chunk ranking into a document ranking (each document at its best chunk)."""
    seen: dict[str, None] = {}
    for chunk_id in chunk_ids:
        document = chunk_document.get(chunk_id)
        if document is not None:
            seen.setdefault(document, None)
    return list(seen)


def answer_subset(cases: list[EvalCase], size: int | None, seed: int) -> set[str]:
    """A deterministic sample of case ids (same seed → same questions in every run/mode)."""
    ids = sorted(c.id for c in cases)
    if size is None or size >= len(ids):
        return set(ids)
    return set(random.Random(seed).sample(ids, size))  # noqa: S311 - reproducible sampling, not crypto


class EvaluationRunner:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        providers: ProviderRegistry,
        settings: Settings,
        pipeline: IngestionPipeline,
        judge: FaithfulnessJudge | None = None,
    ) -> None:
        self.sessionmaker = sessionmaker
        self.providers = providers
        self.settings = settings
        self.pipeline = pipeline
        self.judge = judge

    # ------------------------------------------------------------------ corpus

    async def _collection(self, tenant_id: uuid.UUID, dataset: Dataset) -> uuid.UUID:
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
            return collection.id

    async def _current_model(self, session: AsyncSession) -> EmbeddingModel | None:
        embedder = self.providers.embeddings()
        return await session.scalar(
            select(EmbeddingModel).where(
                EmbeddingModel.provider == embedder.name,
                EmbeddingModel.model == embedder.model,
                EmbeddingModel.version == embedder.version,
            )
        )

    async def _is_stale(self, collection_id: uuid.UUID) -> bool:
        """Stale = no current-model registration yet, or some chunk uses another model."""
        async with self.sessionmaker() as session:
            model = await self._current_model(session)
            if model is None:
                return True
            other = await session.scalar(
                select(Chunk.id)
                .where(Chunk.collection_id == collection_id, Chunk.embedding_model_id != model.id)
                .limit(1)
            )
            return other is not None

    async def prepare_corpus(self, tenant_id: uuid.UUID, dataset: Dataset) -> Corpus:
        collection_id = await self._collection(tenant_id, dataset)
        if dataset.format == "beir":
            doc_key = await self._prepare_bulk(tenant_id, collection_id, dataset.records)
        else:
            doc_key = await self._prepare_files(tenant_id, collection_id, dataset)

        async with self.sessionmaker() as session:
            rows = await session.execute(
                select(Chunk.id, Chunk.document_id, Chunk.text).where(
                    Chunk.collection_id == collection_id
                )
            )
            chunks_by_doc: dict[uuid.UUID, list[tuple[str, str]]] = {}
            chunk_document: dict[str, str] = {}
            for cid, did, text in rows:
                chunks_by_doc.setdefault(did, []).append((str(cid), normalize_answer(text)))
                chunk_document[str(cid)] = str(did)
            model = await self._current_model(session)

        relevant: dict[str, set[str]] = {}
        relevant_docs: dict[str, set[str]] = {}
        for case in dataset.cases:
            relevant_docs[case.id] = {
                str(doc_key[label.doc]) for label in case.relevant if label.doc in doc_key
            }
            ids: set[str] = set()
            for label in case.relevant:
                document_id = doc_key.get(label.doc)
                candidates = chunks_by_doc.get(document_id, []) if document_id else []
                if label.contains is None:
                    matched = {cid for cid, _ in candidates}
                else:
                    needle = normalize_answer(label.contains)
                    matched = {cid for cid, text in candidates if needle in text}
                if not matched:
                    raise DatasetError(f"case {case.id}: label {label} matches no chunk")
                ids |= matched
            relevant[case.id] = ids
        name = f"{model.provider}/{model.model}@{model.version}" if model else "unknown"
        whole_documents = all(
            label.contains is None for case in dataset.cases for label in case.relevant
        )
        if not whole_documents:
            return Corpus(collection_id, relevant, name)
        return Corpus(collection_id, relevant, name, relevant_docs, chunk_document)

    async def _prepare_files(
        self, tenant_id: uuid.UUID, collection_id: uuid.UUID, dataset: Dataset
    ) -> dict[str, uuid.UUID]:
        hash_to_file: dict[str, str] = {}
        for path in dataset.documents:
            content = path.read_bytes()
            digest = hashlib.sha256(content).hexdigest()
            hash_to_file[digest] = path.name
            await self._ensure_document(tenant_id, collection_id, path.name, content, digest)
        if await self._is_stale(collection_id):
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
        return {
            hash_to_file[d.content_hash]: d.id for d in documents if d.content_hash in hash_to_file
        }

    async def _prepare_bulk(
        self, tenant_id: uuid.UUID, collection_id: uuid.UUID, records: list[DocRecord]
    ) -> dict[str, uuid.UUID]:
        """Large public corpora: chunk everything, embed in cross-document batches, insert in
        chunks of documents. Skipped when the collection is already complete and current."""
        async with self.sessionmaker() as session:
            ready = await session.scalar(
                select(func.count())
                .select_from(Document)
                .where(
                    Document.collection_id == collection_id, Document.status == DocumentStatus.READY
                )
            )
        if ready == len(records) and not await self._is_stale(collection_id):
            return await self._external_ids(collection_id)

        logger.info("eval_bulk_ingest_started", documents=len(records))
        async with self.sessionmaker() as session, session.begin():
            await session.execute(delete(Document).where(Document.collection_id == collection_id))
        embedder = self.providers.embeddings()
        async with self.sessionmaker() as session, session.begin():
            model = await ensure_embedding_model(session, embedder)
        started = time.perf_counter()
        for start in range(0, len(records), BULK_DOCS_PER_TRANSACTION):
            batch = records[start : start + BULK_DOCS_PER_TRANSACTION]
            planned = [
                (
                    record,
                    chunk_sections(
                        [Section(record.text)],
                        chunk_size=self.settings.chunk_size,
                        chunk_overlap=self.settings.chunk_overlap,
                    ),
                )
                for record in batch
            ]
            texts = [
                embedding_text(r.title or r.external_id, None, c.text)
                for r, chunks in planned
                for c in chunks
            ]
            vectors = iter(await embedder.embed(texts, input_type="passage"))
            async with self.sessionmaker() as session, session.begin():
                for record, chunks in planned:
                    document = Document(
                        tenant_id=tenant_id,
                        collection_id=collection_id,
                        title=(record.title or record.external_id)[:500],
                        source_type=SourceType.TEXT,
                        mime_type="text/plain",
                        size_bytes=len(record.text.encode()),
                        content_hash=hashlib.sha256(
                            f"{record.external_id}\n{record.text}".encode()
                        ).hexdigest(),
                        status=DocumentStatus.READY,
                        chunk_count=len(chunks),
                        meta={"external_id": record.external_id, "kind": "benchmark"},
                    )
                    session.add(document)
                    await session.flush()
                    session.add_all(
                        Chunk(
                            tenant_id=tenant_id,
                            document_id=document.id,
                            collection_id=collection_id,
                            ordinal=c.ordinal,
                            text=c.text,
                            token_count=c.token_estimate,
                            meta={"kind": "benchmark"},
                            embedding=next(vectors),
                            embedding_model_id=model.id,
                            embedding_dim=model.dimension,
                        )
                        for c in chunks
                    )
            logger.info(
                "eval_bulk_ingest_progress",
                done=min(start + len(batch), len(records)),
                total=len(records),
                elapsed_s=round(time.perf_counter() - started, 1),
            )
        return await self._external_ids(collection_id)

    async def _external_ids(self, collection_id: uuid.UUID) -> dict[str, uuid.UUID]:
        async with self.sessionmaker() as session:
            rows = await session.execute(
                select(Document.id, Document.meta).where(Document.collection_id == collection_id)
            )
            return {
                str(meta.get("external_id")): did for did, meta in rows if meta.get("external_id")
            }

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
            "split": options.split,
            "runs": options.runs,
            "unit": corpus.unit if corpus else None,
            "document_pool": (
                document_pool(RetrievalConfig(top_k=options.top_k)).top_k
                if corpus and corpus.unit == "document"
                else None
            ),
            "top_k": options.top_k,
            "candidate_k": max(self.settings.retrieval_candidate_k, options.top_k),
            "rrf_k": self.settings.rrf_k,
            "lexical_weight": self.settings.rrf_lexical_weight,
            "min_dense_similarity": self.settings.min_dense_similarity,
            "min_lexical_coverage": self.settings.min_lexical_coverage,
            "min_rerank_score": self.settings.min_rerank_score,
            "chunk_size": self.settings.chunk_size,
            "chunk_overlap": self.settings.chunk_overlap,
            "embedding_model": corpus.embedding_model if corpus else None,
            "reranker": f"{reranker.name}/{reranker.model}" if reranker else None,
            "include_answers": answers,
            "answer_sample": options.answer_sample if answers else None,
            "sample_seed": options.sample_seed,
            "provider": chat.name if chat else None,
            "model": chat.model if chat else None,
            "judge": self.judge.name if answers and self.judge else None,
            "max_retrieval_attempts": self.settings.agent_max_retrieval_attempts,
            "max_llm_calls": self.settings.agent_max_llm_calls,
        }

    async def create_runs(self, tenant_id: uuid.UUID, options: EvalOptions) -> list[uuid.UUID]:
        dataset = load_dataset(options.dataset)
        dataset.cases_for(options.split)  # validates the split name
        unknown = set(options.modes) - set(ALL_MODES)
        if unknown:
            raise DatasetError(f"unknown modes {sorted(unknown)}")
        if "hybrid_rerank" in options.modes and self.providers.reranker() is None:
            raise DatasetError("hybrid_rerank requires a configured reranker (RERANKER_PROVIDER)")
        if not 1 <= options.runs <= 10:
            raise DatasetError("runs must be between 1 and 10")
        group = uuid.uuid4().hex
        ids = []
        async with self.sessionmaker() as session, session.begin():
            for run_index in range(options.runs):
                for mode in options.modes:
                    answers = options.include_answers and (
                        options.answer_modes is None or mode in options.answer_modes
                    )
                    config = {
                        **self._config(mode, options, None, answers),
                        "run_group": group,
                        "run_index": run_index,
                    }
                    run = EvaluationRun(
                        tenant_id=tenant_id,
                        dataset_name=dataset.name,
                        dataset_version=dataset.version,
                        retrieval_mode=EvaluationMode(mode),
                        config=config,
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
        cases = [
            c
            for c in dataset.cases_for(options.split)
            if not options.case_ids or c.id in options.case_ids
        ]
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
            run.config = {**run.config, **self._config(mode, options, corpus, answers)}
        config = RetrievalConfig(
            mode=mode,  # type: ignore[arg-type]
            top_k=options.top_k,
            candidate_k=max(self.settings.retrieval_candidate_k, options.top_k),
            rrf_k=self.settings.rrf_k,
            lexical_weight=self.settings.rrf_lexical_weight,
            min_dense_similarity=self.settings.min_dense_similarity,
            min_lexical_coverage=self.settings.min_lexical_coverage,
            min_rerank_score=self.settings.min_rerank_score,
        )
        retriever = Retriever(
            self.providers.embeddings(),
            self.providers.reranker() if mode == "hybrid_rerank" else None,
        )
        vectors: dict[str, list[float]] = {}
        if mode in DENSE_MODES:
            # One batched embedding pass per run (also measures embedding stability across runs).
            embedded = await self.providers.embeddings().embed(
                [c.question for c in cases], input_type="query"
            )
            vectors = {c.id: v for c, v in zip(cases, embedded, strict=True)}
        answered = (
            answer_subset(cases, options.answer_sample, options.sample_seed) if answers else set()
        )
        graph = (
            build_graph(
                InMemorySaver(), max_retrieval_attempts=self.settings.agent_max_retrieval_attempts
            )
            if answers
            else None
        )
        log = logger.bind(run_id=str(run_id), mode=mode)
        log.info("eval_run_started", cases=len(cases), answered=len(answered))

        results: list[dict[str, Any]] = []
        for case in cases:
            results.append(
                await self._run_case(
                    tenant_id,
                    case,
                    corpus,
                    config,
                    retriever,
                    graph if case.id in answered else None,
                    vectors.get(case.id),
                )
            )
            if case.id in answered and options.pause_between_answers:
                await asyncio.sleep(options.pause_between_answers)

        metrics = aggregate(results, cases, answers, judge=self.judge.name if self.judge else None)
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
        query_vector: list[float] | None,
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
                    config=document_pool(config) if corpus.relevant_docs is not None else config,
                    query_vector=query_vector,
                )
            result["retrieval_latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
            retrieved = [str(c.chunk_id) for c in retrieval.chunks]
            if corpus.relevant_docs is not None:
                documents = rank_documents(retrieved, corpus.chunk_document)
                scored = retrieval_metrics(documents, corpus.relevant_docs[case.id], config.top_k)
                retrieved = retrieved[: config.top_k]
            else:
                scored = retrieval_metrics(retrieved, relevant, config.top_k)
            result["retrieved"] = retrieved
            result["retrieval_metrics"] = {
                **scored,
                "sufficient": retrieval.sufficiency.sufficient,
                "top_dense_similarity": retrieval.sufficiency.top_dense_similarity,
                "top_lexical_coverage": retrieval.sufficiency.top_lexical_coverage,
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
                    "provider": state.get("provider"),
                    "model": state.get("model"),
                    "llm_calls": state.get("llm_calls", 0),
                    "prompt_tokens": state.get("prompt_tokens", 0),
                    "completion_tokens": state.get("completion_tokens", 0),
                }
                if self.judge is not None and state.get("outcome") == "answered":
                    claims = claims_from_answer(state)
                    if claims:
                        result["answer_metrics"]["judge_eligible"] = True
                        try:
                            judged = await self.judge.judge(claims)
                        except Exception as exc:
                            # A judge outage (e.g. exhausted quota) leaves the case unjudged; it
                            # is not a generator error and must not change the answer metrics.
                            logger.warning(
                                "eval_judge_failed", case_id=case.id, error_type=type(exc).__name__
                            )
                            result["answer_metrics"]["judge_error"] = type(exc).__name__
                        else:
                            result["answer_metrics"].update(judged.scores())
                            result["answer_metrics"]["judge_verdicts"] = judged.verdicts
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
                    select(Document.title, Document.status, Document.chunk_count)
                    .where(Document.collection_id == corpus.collection_id)
                    .limit(50)
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


ANSWER_KEYS = (
    "correctness",
    "completeness",
    "citation_precision",
    "faithfulness",
    "faithfulness_judged",
    "judge_partial_rate",
    "abstention_accuracy",
    "forbidden_content",
)


def aggregate(
    results: list[dict[str, Any]], cases: list[EvalCase], answers: bool, judge: str | None = None
) -> dict[str, Any]:
    by_case = {c.id: c for c in cases}
    keys = ("recall@k", "precision@k", "hit_rate@k", "mrr", "ndcg@k")
    retrieval = {k: _round(mean([r["retrieval_metrics"].get(k) for r in results])) for k in keys}
    retrieval_latencies = [
        r["retrieval_latency_ms"] for r in results if r["retrieval_latency_ms"] is not None
    ]
    answered = [r for r in results if r["answer_metrics"]]
    latencies = [r["latency_ms"] for r in answered if r["latency_ms"] is not None]
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
        metrics["answered_case_count"] = len(answered)
        metrics["answer"] = {
            k: _round(mean([r["answer_metrics"].get(k) for r in answered])) for k in ANSWER_KEYS
        }
        metrics["answer"]["judge"] = (
            f"deterministic reference checks; faithfulness_judged is model-judged by {judge}"
            if judge
            else "deterministic reference checks (no LLM judge)"
        )
        metrics["answer"]["judged_claims"] = sum(
            r["answer_metrics"].get("judged_claims") or 0 for r in answered
        )
        metrics["answer"]["judge_coverage"] = {
            "eligible_cases": sum(1 for r in answered if r["answer_metrics"].get("judge_eligible")),
            "judged_cases": sum(1 for r in answered if r["answer_metrics"].get("judged_claims")),
            "failed_cases": sum(1 for r in answered if r["answer_metrics"].get("judge_error")),
        }
        metrics["latency_ms"]["end_to_end_p50"] = _round(percentile(latencies, 50), 1)
        metrics["latency_ms"]["end_to_end_p95"] = (
            _round(percentile(latencies, 95), 1) if len(latencies) >= MIN_SAMPLES_FOR_P95 else None
        )
        metrics["tokens"] = {
            "prompt": sum(r["answer_metrics"].get("prompt_tokens") or 0 for r in answered),
            "completion": sum(r["answer_metrics"].get("completion_tokens") or 0 for r in answered),
            "llm_calls": sum(r["answer_metrics"].get("llm_calls") or 0 for r in answered),
        }
    categories: dict[str, dict[str, Any]] = {}
    for r in results:
        bucket = categories.setdefault(
            by_case[r["case_id"]].category,
            {"count": 0, "_recall": [], "_mrr": [], "_correct": [], "_abstain": []},
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
