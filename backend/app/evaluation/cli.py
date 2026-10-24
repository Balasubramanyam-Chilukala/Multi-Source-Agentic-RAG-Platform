"""Reproducible benchmark command.

    uv run python -m app.cli evaluate --modes all          # retrieval only, configured providers
    uv run python -m app.cli evaluate --modes all --mock   # fully offline, deterministic
    uv run python -m app.cli evaluate --modes hybrid --answers --pause 2

Results are stored in PostgreSQL (``evaluation_runs``) under a dedicated evaluation workspace
and written to ``eval/results/<dataset>/<timestamp>.{json,md}``.
"""

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.db.models.evaluation import EvaluationCaseResult, EvaluationRun
from app.db.models.tenancy import Tenant
from app.db.session import Database
from app.evaluation.dataset import load_dataset
from app.evaluation.report import write_results
from app.evaluation.runner import ALL_MODES, EvalOptions, EvaluationRunner
from app.ingestion.pipeline import IngestionPipeline
from app.providers.factory import ProviderRegistry

logger = get_logger("atlas.evaluation.cli")
EVAL_TENANT_SLUG = "atlas-evaluation"
RESULTS_DIR = Path(__file__).resolve().parents[2] / "eval" / "results"


async def _tenant(database: Database) -> Any:
    async with database.sessionmaker() as session, session.begin():
        tenant = await session.scalar(select(Tenant).where(Tenant.slug == EVAL_TENANT_SLUG))
        if tenant is None:
            tenant = Tenant(name="Evaluation", slug=EVAL_TENANT_SLUG)
            session.add(tenant)
            await session.flush()
        return tenant.id


async def run(settings: Settings, options: EvalOptions, out_dir: Path) -> Path:
    database = Database.from_settings(settings)
    providers = ProviderRegistry(settings)
    try:
        pipeline = IngestionPipeline(database.sessionmaker, providers, settings, max_concurrency=1)
        runner = EvaluationRunner(database.sessionmaker, providers, settings, pipeline)
        tenant_id = await _tenant(database)
        run_ids = await runner.create_runs(tenant_id, options)
        await runner.execute(tenant_id, run_ids, options)
        dataset = load_dataset(options.dataset)
        questions = {c.id: c for c in dataset.cases}
        collected = []
        async with database.sessionmaker() as session:
            for run_id in run_ids:
                row = await session.get(EvaluationRun, run_id)
                if row is None:
                    continue
                if row.error_message:
                    raise RuntimeError(
                        f"run {row.retrieval_mode.value} failed: {row.error_message}"
                    )
                cases = await session.scalars(
                    select(EvaluationCaseResult)
                    .where(EvaluationCaseResult.evaluation_run_id == run_id)
                    .order_by(EvaluationCaseResult.case_id)
                )
                collected.append(
                    {
                        "run_id": str(row.id),
                        "dataset": row.dataset_name,
                        "dataset_version": row.dataset_version,
                        "mode": row.retrieval_mode.value,
                        "config": row.config,
                        "metrics": row.metrics,
                        "cases": [
                            {
                                "case_id": c.case_id,
                                "category": questions[c.case_id].category,
                                "question": questions[c.case_id].question,
                                "retrieved_chunk_ids": c.retrieved_chunk_ids,
                                "retrieval_metrics": c.retrieval_metrics,
                                "answer_metrics": c.answer_metrics,
                                "answer": c.answer,
                                "latency_ms": c.latency_ms,
                                "error": c.error_message,
                            }
                            for c in cases
                        ],
                    }
                )
        title = f"Atlas AI benchmark — {options.dataset}"
        return write_results(collected, out_dir, title=title)
    finally:
        await providers.aclose()
        await database.dispose()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli evaluate")
    parser.add_argument("--dataset", default="atlas-handbook-v1")
    parser.add_argument(
        "--modes",
        default="all",
        help="comma-separated: dense,lexical,hybrid,hybrid_rerank or 'all'",
    )
    parser.add_argument(
        "--answers", action="store_true", help="also run the agent and score answers (uses the LLM)"
    )
    parser.add_argument(
        "--answer-modes", default=None, help="limit --answers to these modes (comma-separated)"
    )
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--cases", default=None, help="comma-separated case ids (default: all)")
    parser.add_argument(
        "--pause", type=float, default=0.0, help="seconds between answered cases (rate limits)"
    )
    parser.add_argument(
        "--mock", action="store_true", help="use deterministic mock providers (offline)"
    )
    parser.add_argument("--reranker", choices=["none", "local", "mock", "nvidia"], default=None)
    parser.add_argument("--out", type=Path, default=RESULTS_DIR)
    args = parser.parse_args(argv)

    overrides: dict[str, Any] = {}
    if args.mock:
        overrides.update(
            llm_provider="mock",
            embedding_provider="mock",
            reranker_provider="mock",
            reranker_model="term-coverage",
            llm_fallback_chain=[],
        )
    if args.reranker:
        overrides["reranker_provider"] = args.reranker
        if args.reranker == "local":
            overrides["reranker_model"] = "Xenova/ms-marco-MiniLM-L-6-v2"
        elif args.reranker == "mock":
            overrides["reranker_model"] = "term-coverage"
    settings = (
        Settings(**{**get_settings().model_dump(), **overrides}) if overrides else get_settings()
    )

    modes = list(ALL_MODES) if args.modes == "all" else [m.strip() for m in args.modes.split(",")]
    if "hybrid_rerank" in modes and settings.reranker_provider == "none":
        print(
            "note: no reranker configured; skipping hybrid_rerank (use --reranker local or --mock)",
            file=sys.stderr,
        )
        modes.remove("hybrid_rerank")
    options = EvalOptions(
        dataset=args.dataset,
        modes=modes,
        include_answers=args.answers,
        answer_modes=[m.strip() for m in args.answer_modes.split(",")]
        if args.answer_modes
        else None,
        top_k=args.top_k,
        case_ids=args.cases.split(",") if args.cases else None,
        pause_between_answers=args.pause,
    )
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    report = asyncio.run(run(settings, options, args.out), loop_factory=loop_factory)
    print(f"report written to {report}")
