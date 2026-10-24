"""Reproducible benchmark command.

    uv run python -m app.cli evaluate --modes all --mock                       # offline, deterministic
    uv run python -m app.cli evaluate --dataset scifact --modes all --runs 3   # public corpus, held-out test
    uv run python -m app.cli evaluate --modes hybrid --answers --runs 3 --judge gemini:gemini-3.6-flash
    uv run python -m app.cli evaluate --judge-calibration --judge gemini:gemini-3.6-flash
    uv run python -m app.cli evaluate --calibrate-threshold                     # tunes on the dev split only

Reported numbers use the ``test`` split; ``--split dev`` is for tuning. During benchmarks the
generator is pinned to the configured model (no fallback chain, patient retries) so a run is
never a silent mix of models; ``--allow-fallback`` turns the chain back on.

Results are stored in PostgreSQL (``evaluation_runs``) under a dedicated evaluation workspace
and written to ``eval/results/<dataset>/<timestamp>.{json,md}``.
"""

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.core.config import Settings, get_settings, parse_chain_entry
from app.core.logging import get_logger
from app.db.models.evaluation import EvaluationCaseResult, EvaluationRun
from app.db.models.tenancy import Tenant
from app.db.session import Database
from app.evaluation.dataset import EVAL_DIR, load_dataset
from app.evaluation.judge import VERDICTS, ClaimToJudge, FaithfulnessJudge
from app.evaluation.report import write_results
from app.evaluation.runner import ALL_MODES, EvalOptions, EvaluationRunner
from app.ingestion.pipeline import IngestionPipeline
from app.providers.factory import ProviderRegistry

logger = get_logger("atlas.evaluation.cli")
EVAL_TENANT_SLUG = "atlas-evaluation"
RESULTS_DIR = EVAL_DIR / "results"
CALIBRATION_FILE = EVAL_DIR / "judge_calibration.jsonl"


async def _tenant(database: Database) -> Any:
    async with database.sessionmaker() as session, session.begin():
        tenant = await session.scalar(select(Tenant).where(Tenant.slug == EVAL_TENANT_SLUG))
        if tenant is None:
            tenant = Tenant(name="Evaluation", slug=EVAL_TENANT_SLUG)
            session.add(tenant)
            await session.flush()
        return tenant.id


def _judge_settings(base: Settings, spec: str) -> Settings:
    provider, model = parse_chain_entry(spec)
    return Settings(
        **{
            **base.model_dump(),
            "llm_provider": provider,
            "llm_model": model,
            "llm_fallback_chain": [],
            "provider_max_retries": 6,
            "provider_max_backoff_seconds": 60.0,
            "llm_temperature": 0.0,
        }
    )


async def _collect(
    database: Database, run_ids: list[Any], dataset_name: str
) -> list[dict[str, Any]]:
    questions = {c.id: c for c in load_dataset(dataset_name).cases}
    collected = []
    async with database.sessionmaker() as session:
        for run_id in run_ids:
            row = await session.get(EvaluationRun, run_id)
            if row is None:
                continue
            if row.error_message:
                raise RuntimeError(f"run {row.retrieval_mode.value} failed: {row.error_message}")
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
    return collected


async def run(
    settings: Settings, options: EvalOptions, out_dir: Path, judge_spec: str | None
) -> Path:
    database = Database.from_settings(settings)
    providers = ProviderRegistry(settings)
    judge_providers = (
        ProviderRegistry(_judge_settings(settings, judge_spec)) if judge_spec else None
    )
    try:
        pipeline = IngestionPipeline(database.sessionmaker, providers, settings, max_concurrency=1)
        judge = FaithfulnessJudge(judge_providers.chat()) if judge_providers else None
        runner = EvaluationRunner(database.sessionmaker, providers, settings, pipeline, judge=judge)
        tenant_id = await _tenant(database)
        run_ids = await runner.create_runs(tenant_id, options)
        await runner.execute(tenant_id, run_ids, options)
        collected = await _collect(database, run_ids, options.dataset)
        title = f"Atlas AI benchmark — {options.dataset} ({options.split} split)"
        return write_results(collected, out_dir, title=title)
    finally:
        await providers.aclose()
        if judge_providers:
            await judge_providers.aclose()
        await database.dispose()


async def judge_calibration(settings: Settings, judge_spec: str, out_dir: Path) -> Path:
    """Agreement of the judge with hand labels (eval/judge_calibration.jsonl)."""
    pairs = [
        json.loads(line)
        for line in CALIBRATION_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    providers = ProviderRegistry(_judge_settings(settings, judge_spec))
    try:
        judge = FaithfulnessJudge(providers.chat(), max_claims_per_call=8)
        result = await judge.judge([ClaimToJudge(p["claim"], p["passages"]) for p in pairs])
    finally:
        await providers.aclose()
    rows = [
        {"id": p["id"], "gold": p["gold"], "predicted": v, "note": p.get("note", "")}
        for p, v in zip(pairs, result.verdicts, strict=True)
    ]
    labelled = [r for r in rows if r["predicted"] is not None]
    exact = sum(r["gold"] == r["predicted"] for r in labelled) / len(labelled) if labelled else 0.0
    binary = (
        sum((r["gold"] == "supported") == (r["predicted"] == "supported") for r in labelled)
        / len(labelled)
        if labelled
        else 0.0
    )
    false_support = [
        r for r in labelled if r["predicted"] == "supported" and r["gold"] != "supported"
    ]
    confusion = {
        g: {p: sum(1 for r in labelled if r["gold"] == g and r["predicted"] == p) for p in VERDICTS}
        for g in VERDICTS
    }
    summary = {
        "judge": judge.name,
        "pairs": len(rows),
        "unlabelled": len(rows) - len(labelled),
        "exact_agreement": round(exact, 4),
        "binary_agreement": round(binary, 4),
        "false_supported": len(false_support),
        "confusion": confusion,
        "rows": rows,
        "tokens": {"prompt": result.prompt_tokens, "completion": result.completion_tokens},
    }
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = out_dir / "judge-calibration"
    target.mkdir(parents=True, exist_ok=True)
    (target / f"{stamp}.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    lines = [
        f"# Faithfulness judge calibration — `{judge.name}`",
        "",
        f"- {len(rows)} hand-labelled (claim, passages) pairs; {summary['unlabelled']} without a usable verdict",
        f"- Exact agreement (3 labels): **{exact:.3f}**",
        f"- Binary agreement (supported vs. not): **{binary:.3f}**",
        f"- False 'supported' verdicts (the costly error for a faithfulness metric): **{len(false_support)}**",
        "",
        "| gold \\ predicted | " + " | ".join(VERDICTS) + " |",
        "|---|---|---|---|",
        *[
            f"| {g} | " + " | ".join(str(confusion[g][p]) for p in VERDICTS) + " |"
            for g in VERDICTS
        ],
        "",
        "Disagreements:",
        "",
        *[
            f"- `{r['id']}` gold={r['gold']} predicted={r['predicted']} — {r['note']}"
            for r in labelled
            if r["gold"] != r["predicted"]
        ],
        "",
    ]
    (target / f"{stamp}.md").write_text("\n".join(lines), encoding="utf-8")
    return target / f"{stamp}.md"


async def calibrate_threshold(
    settings: Settings, dataset: str, target_pass: float, out_dir: Path
) -> Path:
    """Choose MIN_DENSE_SIMILARITY on the dev split: the largest threshold that still lets
    ``target_pass`` of answerable dev questions through the retrieval gate."""
    options = EvalOptions(dataset=dataset, modes=["hybrid"], split="dev", runs=1)
    database = Database.from_settings(settings)
    providers = ProviderRegistry(settings)
    try:
        runner = EvaluationRunner(
            database.sessionmaker,
            providers,
            settings,
            IngestionPipeline(database.sessionmaker, providers, settings, max_concurrency=1),
        )
        tenant_id = await _tenant(database)
        run_ids = await runner.create_runs(tenant_id, options)
        await runner.execute(tenant_id, run_ids, options)
        collected = await _collect(database, run_ids, dataset)
    finally:
        await providers.aclose()
        await database.dispose()
    cases = {c.id: c for c in load_dataset(dataset).cases}
    points = [
        (
            cases[c["case_id"]].answerable,
            c["retrieval_metrics"].get("top_dense_similarity") or 0.0,
            c["retrieval_metrics"].get("top_lexical_coverage") or 0.0,
        )
        for c in collected[0]["cases"]
    ]
    coverage_gate = settings.min_lexical_coverage
    grid = [round(0.05 + 0.01 * i, 2) for i in range(66)]

    answerable = [(d, cov) for a, d, cov in points if a]
    unanswerable = [(d, cov) for a, d, cov in points if not a]

    def rates(t: float) -> tuple[float, float]:
        """(share of answerable dev questions let through, share of unanswerable ones blocked)."""
        passes = [d >= t or cov >= coverage_gate for d, cov in answerable]
        blocks = [not (d >= t or cov >= coverage_gate) for d, cov in unanswerable]
        return sum(passes) / max(1, len(passes)), sum(blocks) / max(1, len(blocks))

    chosen = max((t for t in grid if rates(t)[0] >= target_pass), default=grid[0])
    pass_rate, block_rate = rates(chosen)
    summary = {
        "dataset": dataset,
        "split": "dev",
        "target_answerable_pass_rate": target_pass,
        "chosen_min_dense_similarity": chosen,
        "dev_answerable_pass_rate": round(pass_rate, 4),
        "dev_unanswerable_block_rate": round(block_rate, 4),
        "min_lexical_coverage": coverage_gate,
        "embedding_model": collected[0]["config"].get("embedding_model"),
        "curve": [
            {"threshold": t, "pass": round(rates(t)[0], 4), "block": round(rates(t)[1], 4)}
            for t in grid
        ],
    }
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = out_dir / dataset
    target.mkdir(parents=True, exist_ok=True)
    path = target / f"threshold-calibration-{stamp}.json"
    path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "curve"}, indent=2))
    return path


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli evaluate")
    parser.add_argument("--dataset", default="atlas-handbook-v1")
    parser.add_argument(
        "--modes",
        default="all",
        help="comma-separated: dense,lexical,hybrid,hybrid_rerank or 'all'",
    )
    parser.add_argument(
        "--split",
        default="test",
        choices=["dev", "test", "all"],
        help="report on test; tune on dev",
    )
    parser.add_argument(
        "--runs", type=int, default=1, help="repetitions (results report mean ± sd)"
    )
    parser.add_argument(
        "--answers", action="store_true", help="also run the agent and score answers (uses the LLM)"
    )
    parser.add_argument(
        "--answer-modes", default=None, help="limit --answers to these modes (comma-separated)"
    )
    parser.add_argument(
        "--answer-sample", type=int, default=None, help="answer a fixed random subset of N cases"
    )
    parser.add_argument(
        "--judge",
        default=None,
        help="faithfulness judge as provider:model, e.g. gemini:gemini-3.6-flash",
    )
    parser.add_argument(
        "--judge-calibration", action="store_true", help="measure judge agreement with hand labels"
    )
    parser.add_argument(
        "--calibrate-threshold",
        action="store_true",
        help="choose MIN_DENSE_SIMILARITY on the dev split",
    )
    parser.add_argument(
        "--target-pass",
        type=float,
        default=0.95,
        help="answerable pass rate for --calibrate-threshold",
    )
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--cases", default=None, help="comma-separated case ids (default: all)")
    parser.add_argument(
        "--pause", type=float, default=0.0, help="seconds between answered cases (rate limits)"
    )
    parser.add_argument(
        "--mock", action="store_true", help="use deterministic mock providers (offline)"
    )
    parser.add_argument(
        "--allow-fallback", action="store_true", help="keep LLM_FALLBACK_CHAIN during the benchmark"
    )
    parser.add_argument("--reranker", choices=["none", "local", "mock", "nvidia"], default=None)
    parser.add_argument("--out", type=Path, default=RESULTS_DIR)
    args = parser.parse_args(argv)

    overrides: dict[str, Any] = {}
    if not args.allow_fallback:
        # One pinned generator per benchmark, with patient retries instead of failover.
        overrides.update(
            llm_fallback_chain=[], provider_max_retries=6, provider_max_backoff_seconds=60.0
        )
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
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None

    if args.judge_calibration:
        if not args.judge:
            parser.error("--judge-calibration needs --judge provider:model")
        path = asyncio.run(
            judge_calibration(settings, args.judge, args.out), loop_factory=loop_factory
        )
        print(f"judge calibration written to {path}")
        return
    if args.calibrate_threshold:
        path = asyncio.run(
            calibrate_threshold(settings, args.dataset, args.target_pass, args.out),
            loop_factory=loop_factory,
        )
        print(f"threshold calibration written to {path}")
        return

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
        split=args.split,
        runs=args.runs,
        include_answers=args.answers,
        answer_modes=[m.strip() for m in args.answer_modes.split(",")]
        if args.answer_modes
        else None,
        answer_sample=args.answer_sample,
        top_k=args.top_k,
        case_ids=args.cases.split(",") if args.cases else None,
        pause_between_answers=args.pause,
    )
    report = asyncio.run(
        run(settings, options, args.out, args.judge if args.answers else None),
        loop_factory=loop_factory,
    )
    print(f"report written to {report}")
