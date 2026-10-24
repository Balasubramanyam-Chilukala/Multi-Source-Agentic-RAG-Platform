"""Machine-readable results and a Markdown comparison report for a set of evaluation runs."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

RETRIEVAL_KEYS = ("recall@k", "precision@k", "hit_rate@k", "mrr", "ndcg@k")
ANSWER_KEYS = (
    "correctness",
    "completeness",
    "citation_precision",
    "faithfulness",
    "abstention_accuracy",
    "forbidden_content",
)


def _fmt(value: Any, digits: int = 3) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def render_markdown(runs: list[dict[str, Any]], *, title: str) -> str:
    """``runs``: [{"mode", "config", "metrics", "cases"}] from one benchmark invocation."""
    first = runs[0]
    lines = [
        f"# {title}",
        "",
        f"- Dataset: `{first['dataset']}` v{first['dataset_version']} — {first['metrics']['case_count']} cases "
        f"({first['metrics']['labelled_case_count']} with relevance labels)",
        f"- k = {first['config']['top_k']}, candidate pool = {first['config']['candidate_k']}, RRF k = {first['config']['rrf_k']}",
        f"- Embedding model: `{first['config']['embedding_model']}`; chunk size {first['config']['chunk_size']} / "
        f"overlap {first['config']['chunk_overlap']}",
        f"- Generated: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        "## Retrieval (same queries, labels and k for every mode)",
        "",
        "| Mode | Recall@k | Precision@k | Hit rate@k | MRR | nDCG@k | p50 ms | p95 ms |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for run in runs:
        m = run["metrics"]
        lines.append(
            f"| {run['mode']} | "
            + " | ".join(_fmt(m["retrieval"][k]) for k in RETRIEVAL_KEYS)
            + f" | {_fmt(m['latency_ms']['retrieval_p50'], 1)} | {_fmt(m['latency_ms']['retrieval_p95'], 1)} |"
        )
    rerankers = ", ".join(
        f"{r['mode']} → `{r['config']['reranker']}`" for r in runs if r["config"]["reranker"]
    )
    lines += ["", f"Reranker: {rerankers or 'none'}", ""]

    answer_runs = [r for r in runs if "answer" in r["metrics"]]
    if answer_runs:
        lines += [
            "## Answers (deterministic reference checks; no LLM judge)",
            "",
            "| Mode | Provider/model | Correctness | Completeness | Citation precision | Faithfulness* | Abstention acc. | Forbidden content | e2e p50 ms | e2e p95 ms | LLM calls | Tokens (prompt/completion) | Error rate |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for run in answer_runs:
            m = run["metrics"]
            lines.append(
                f"| {run['mode']} | `{run['config']['provider']}/{run['config']['model']}` | "
                + " | ".join(_fmt(m["answer"][k]) for k in ANSWER_KEYS)
                + f" | {_fmt(m['latency_ms'].get('end_to_end_p50'), 0)} | {_fmt(m['latency_ms'].get('end_to_end_p95'), 0)}"
                + f" | {m['tokens']['llm_calls']} | {m['tokens']['prompt']}/{m['tokens']['completion']} | {_fmt(m['error_rate'])} |"
            )
        lines += [
            "",
            "\\* Faithfulness here = share of answer sentences that carry a valid citation; it does not verify entailment.",
            "",
        ]

    categories = sorted({c for r in runs for c in r["metrics"]["by_category"]})
    lines += [
        "## Recall@k by category",
        "",
        "| Category | n | " + " | ".join(r["mode"] for r in runs) + " |",
        "|---|---|" + "---|" * len(runs),
    ]
    for category in categories:
        count = runs[0]["metrics"]["by_category"].get(category, {}).get("count", 0)
        lines.append(
            f"| {category} | {count} | "
            + " | ".join(
                _fmt(r["metrics"]["by_category"].get(category, {}).get("recall@k")) for r in runs
            )
            + " |"
        )
    if answer_runs:
        lines += [
            "",
            "## Answer correctness / abstention accuracy by category",
            "",
            "| Category | " + " | ".join(r["mode"] for r in answer_runs) + " |",
            "|---|" + "---|" * len(answer_runs),
        ]
        for category in categories:
            cells = []
            for r in answer_runs:
                b = r["metrics"]["by_category"].get(category, {})
                cells.append(
                    f"{_fmt(b.get('correctness'), 2)} / {_fmt(b.get('abstention_accuracy'), 2)}"
                )
            lines.append(f"| {category} | " + " | ".join(cells) + " |")

    lines += ["", "## Where each mode improves or degrades (vs. hybrid)", ""]
    baseline = next((r for r in runs if r["mode"] == "hybrid"), None)
    if baseline:
        base_cases = {c["case_id"]: c for c in baseline["cases"]}
        for run in runs:
            if run is baseline:
                continue
            better, worse = [], []
            for case in run["cases"]:
                ref = base_cases.get(case["case_id"])
                a, b = (
                    case["retrieval_metrics"].get("mrr"),
                    ref and ref["retrieval_metrics"].get("mrr"),
                )
                if a is None or b is None:
                    continue
                if a > b + 1e-9:
                    better.append(case["case_id"])
                elif a < b - 1e-9:
                    worse.append(case["case_id"])
            lines.append(
                f"- **{run['mode']}**: better MRR on {len(better)} cases ({', '.join(better[:12]) or 'none'}); "
                f"worse on {len(worse)} ({', '.join(worse[:12]) or 'none'})."
            )
    lines += [
        "",
        "_Generated by `python -m app.cli evaluate`. Raw per-case results are in the JSON files next to this report._",
        "",
    ]
    return "\n".join(lines)


def write_results(runs: list[dict[str, Any]], out_dir: Path, *, title: str) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = out_dir / runs[0]["dataset"]
    target.mkdir(parents=True, exist_ok=True)
    payload = {"generated_at": stamp, "runs": runs}
    (target / f"{stamp}.json").write_text(
        json.dumps(payload, indent=2, default=str), encoding="utf-8"
    )
    report_path: Path = target / f"{stamp}.md"
    report_path.write_text(render_markdown(runs, title=title), encoding="utf-8")
    return report_path
