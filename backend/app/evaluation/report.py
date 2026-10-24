"""Machine-readable results and a Markdown report for one benchmark invocation.

Repeated runs of the same mode are aggregated as mean ± sample standard deviation. Differences
between retrieval modes are reported with a paired bootstrap 95% confidence interval over the
evaluated queries, so "mode A beats mode B" is only claimed when the interval excludes zero.
"""

import json
import random
import statistics
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

RETRIEVAL_KEYS = ("recall@k", "precision@k", "hit_rate@k", "mrr", "ndcg@k")
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
BOOTSTRAP_SAMPLES = 2000
BOOTSTRAP_SEED = 7


def _fmt(value: Any, digits: int = 3) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def mean_sd(values: list[float | None]) -> tuple[float | None, float | None]:
    present = [v for v in values if v is not None]
    if not present:
        return None, None
    return statistics.fmean(present), (statistics.stdev(present) if len(present) > 1 else 0.0)


def _cell(values: list[float | None], digits: int = 3) -> str:
    m, sd = mean_sd(values)
    if m is None:
        return "—"
    return f"{m:.{digits}f}" if len(values) == 1 else f"{m:.{digits}f} ± {sd:.{digits}f}"


def paired_bootstrap(
    a: list[float], b: list[float], seed: int = BOOTSTRAP_SEED
) -> tuple[float, float, float]:
    """Mean of (a - b) with a 95% percentile-bootstrap CI over paired observations."""
    diffs = [x - y for x, y in zip(a, b, strict=True)]
    rng = random.Random(seed)
    n = len(diffs)
    means = sorted(statistics.fmean(rng.choices(diffs, k=n)) for _ in range(BOOTSTRAP_SAMPLES))
    return (
        statistics.fmean(diffs),
        means[int(0.025 * BOOTSTRAP_SAMPLES)],
        means[int(0.975 * BOOTSTRAP_SAMPLES) - 1],
    )


def _per_case(run: dict[str, Any], key: str) -> dict[str, float]:
    return {
        c["case_id"]: c["retrieval_metrics"][key]
        for c in run["cases"]
        if c["retrieval_metrics"].get(key) is not None
    }


def render_markdown(runs: list[dict[str, Any]], *, title: str) -> str:
    by_mode: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for run in runs:
        by_mode[run["mode"]].append(run)
    first = runs[0]
    cfg = first["config"]
    repetitions = max(len(v) for v in by_mode.values())
    deterministic = repetitions > 1 and all(
        len({tuple(r["metrics"]["retrieval"][k] for k in RETRIEVAL_KEYS) for r in rs}) == 1
        for rs in by_mode.values()
    )
    unit = cfg.get("unit") or "chunk"
    lines = [
        f"# {title}",
        "",
        f"- Dataset: `{first['dataset']}` {first['dataset_version']}, split **{cfg.get('split', 'all')}**: "
        f"{first['metrics']['case_count']} cases ({first['metrics']['labelled_case_count']} with relevance labels)",
        f"- Repetitions per mode: **{repetitions}** (cells show mean ± sd when > 1; query embeddings are recomputed each run)",
        *(
            [
                "- All repetitions produced identical retrieval metrics (the embedding API and the "
                "rankers are deterministic), so sd = 0 says nothing about sampling uncertainty; the "
                "paired bootstrap CIs over queries below are the meaningful error bars."
            ]
            if deterministic
            else []
        ),
        f"- Scoring unit: **{unit}**"
        + (
            f" (a pool of {cfg.get('document_pool')} chunks collapsed to their documents; top k distinct documents vs. qrels, as in BEIR)"
            if unit == "document"
            else " (labels name the passage that contains the answer)"
        ),
        f"- k = {cfg['top_k']}, candidate pool = {cfg['candidate_k']}, RRF k = {cfg['rrf_k']}",
        f"- Embedding model: `{cfg['embedding_model']}`; chunk size {cfg['chunk_size']} / overlap {cfg['chunk_overlap']}",
        f"- Generated: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        "## Retrieval (same queries, labels and k for every mode)",
        "",
        "| Mode | Recall@k | Precision@k | Hit rate@k | MRR | nDCG@k | p50 ms |",
        "|---|---|---|---|---|---|---|",
    ]
    for mode, mode_runs in by_mode.items():
        lines.append(
            f"| {mode} | "
            + " | ".join(
                _cell([r["metrics"]["retrieval"][k] for r in mode_runs]) for k in RETRIEVAL_KEYS
            )
            + f" | {_cell([r['metrics']['latency_ms']['retrieval_p50'] for r in mode_runs], 0)} |"
        )
    rerankers = ", ".join(
        sorted(
            {f"{r['mode']} → `{r['config']['reranker']}`" for r in runs if r["config"]["reranker"]}
        )
    )
    lines += ["", f"Reranker: {rerankers or 'none'}", ""]

    if "hybrid" in by_mode and len(by_mode) > 1:
        lines += [
            "## Paired comparison vs. hybrid (MRR and nDCG@k, first repetition, 95% bootstrap CI)",
            "",
            "| Mode | n | Δ MRR | 95% CI | Δ nDCG@k | 95% CI | Verdict |",
            "|---|---|---|---|---|---|---|",
        ]
        base = by_mode["hybrid"][0]
        for mode, mode_runs in by_mode.items():
            if mode == "hybrid":
                continue
            cells: list[str] = []
            verdict = "no significant difference"
            for key in ("mrr", "ndcg@k"):
                ours, theirs = _per_case(mode_runs[0], key), _per_case(base, key)
                shared = sorted(set(ours) & set(theirs))
                if len(shared) < 2:
                    cells += ["—", "—"]
                    continue
                diff, low, high = paired_bootstrap(
                    [ours[c] for c in shared], [theirs[c] for c in shared]
                )
                cells += [f"{diff:+.3f}", f"[{low:+.3f}, {high:+.3f}]"]
                if key == "mrr" and (low > 0 or high < 0):
                    verdict = "better than hybrid" if low > 0 else "worse than hybrid"
            n = len(set(_per_case(mode_runs[0], "mrr")) & set(_per_case(base, "mrr")))
            lines.append(f"| {mode} | {n} | " + " | ".join(cells) + f" | {verdict} |")
        lines.append("")

    answer_modes = {m: rs for m, rs in by_mode.items() if "answer" in rs[0]["metrics"]}
    if answer_modes:
        judge = next(
            (
                r["config"].get("judge")
                for rs in answer_modes.values()
                for r in rs
                if r["config"].get("judge")
            ),
            None,
        )
        lines += [
            "## Answers",
            "",
            "Deterministic reference checks, plus a model-judged faithfulness score"
            + (
                f" (judge: `{judge}`, a different model family from the generator)."
                if judge
                else " (no judge configured)."
            ),
            "",
            "| Mode | Generator | Answered | Correctness | Completeness | Citation precision | Faithfulness (proxy)* | Faithfulness (judged)† | Partial (judged) | Abstention acc. | Forbidden content | e2e p50 ms |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for mode, mode_runs in answer_modes.items():
            generator = f"`{mode_runs[0]['config']['provider']}/{mode_runs[0]['config']['model']}`"
            answered = mode_runs[0]["metrics"].get(
                "answered_case_count", mode_runs[0]["metrics"]["case_count"]
            )
            lines.append(
                f"| {mode} | {generator} | {answered} | "
                + " | ".join(
                    _cell([r["metrics"]["answer"].get(k) for r in mode_runs]) for k in ANSWER_KEYS
                )
                + f" | {_cell([r['metrics']['latency_ms'].get('end_to_end_p50') for r in mode_runs], 0)} |"
            )
        lines += [
            "",
            "\\* Proxy: share of answer sentences carrying a valid citation (structure only, no entailment check).",
            "† Judged: share of cited sentences the judge labelled *supported* by the passages they cite "
            "(model-judged, not ground truth; see the judge calibration report for its agreement with hand labels).",
            "",
        ]

    categories = sorted({c for r in runs for c in r["metrics"]["by_category"]})
    if len(categories) > 1:
        lines += [
            "## Recall@k by category (first repetition)",
            "",
            "| Category | n | " + " | ".join(by_mode) + " |",
            "|---|---|" + "---|" * len(by_mode),
        ]
        for category in categories:
            count = first["metrics"]["by_category"].get(category, {}).get("count", 0)
            lines.append(
                f"| {category} | {count} | "
                + " | ".join(
                    _fmt(rs[0]["metrics"]["by_category"].get(category, {}).get("recall@k"))
                    for rs in by_mode.values()
                )
                + " |"
            )
        lines.append("")
    lines += [
        "_Generated by `python -m app.cli evaluate`. Raw per-case results are in the JSON file next to this report._",
        "",
    ]
    return "\n".join(lines)


def write_results(runs: list[dict[str, Any]], out_dir: Path, *, title: str) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = out_dir / runs[0]["dataset"]
    target.mkdir(parents=True, exist_ok=True)
    (target / f"{stamp}.json").write_text(
        json.dumps({"generated_at": stamp, "runs": runs}, indent=2, default=str), encoding="utf-8"
    )
    report_path: Path = target / f"{stamp}.md"
    report_path.write_text(render_markdown(runs, title=title), encoding="utf-8")
    return report_path
