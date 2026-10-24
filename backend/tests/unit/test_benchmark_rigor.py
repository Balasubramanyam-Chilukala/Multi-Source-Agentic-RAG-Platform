"""Benchmark rigor: splits, the BEIR loader (checksum-pinned), the faithfulness judge's parsing,
deterministic answer sampling, and paired-bootstrap statistics."""

import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest

from app.evaluation import dataset as ds
from app.evaluation.dataset import DatasetError, EvalCase, RelevanceLabel, load_dataset
from app.evaluation.judge import ClaimToJudge, FaithfulnessJudge, claims_from_answer, parse_verdicts
from app.evaluation.report import mean_sd, paired_bootstrap
from app.evaluation.runner import aggregate, answer_subset, document_pool, rank_documents
from app.providers.base import ChatMessage, ChatResult, Usage
from app.retrieval.types import RetrievalConfig


def test_handbook_has_disjoint_stratified_dev_and_test_splits() -> None:
    dataset = load_dataset("atlas-handbook-v1")
    dev, test = dataset.cases_for("dev"), dataset.cases_for("test")
    assert len(dev) + len(test) == len(dataset.cases) == 60
    assert not {c.id for c in dev} & {c.id for c in test}
    # Every category appears in both splits, so tuning on dev cannot ignore a whole category.
    assert {c.category for c in dev} == {c.category for c in test}
    with pytest.raises(DatasetError):
        dataset.cases_for("train")


def _beir_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "tiny/corpus.jsonl",
            "\n".join(
                json.dumps(d)
                for d in [
                    {
                        "_id": "d1",
                        "title": "Vitamin D",
                        "text": "Vitamin D did not reduce fractures.",
                    },
                    {
                        "_id": "d2",
                        "title": "Sleep",
                        "text": "Short sleep is associated with higher BMI.",
                    },
                ]
            ),
        )
        archive.writestr(
            "tiny/queries.jsonl",
            "\n".join(
                json.dumps(q)
                for q in [
                    {"_id": "1", "text": "Vitamin D prevents fractures."},
                    {"_id": "2", "text": "Sleep affects BMI."},
                    {"_id": "3", "text": "Unjudged query."},
                ]
            ),
        )
        archive.writestr("tiny/qrels/train.tsv", "query-id\tcorpus-id\tscore\n1\td1\t1\n")
        archive.writestr(
            "tiny/qrels/test.tsv", "query-id\tcorpus-id\tscore\n2\td2\t1\n2\tmissing\t1\n"
        )
    return buffer.getvalue()


def _install_tiny_beir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, sha: str | None = None
) -> None:
    content = _beir_zip()
    datasets_dir, cache_dir = tmp_path / "datasets", tmp_path / "cache"
    (datasets_dir / "tiny").mkdir(parents=True)
    (cache_dir / "tiny").mkdir(parents=True)
    (cache_dir / "tiny" / "tiny.zip").write_bytes(content)
    manifest = {
        "name": "tiny",
        "version": "1",
        "format": "beir",
        "archive_url": "https://example.invalid/tiny.zip",
        "archive_prefix": "tiny",
        "sha256": sha or hashlib.sha256(content).hexdigest(),
        "category": "scientific_claim",
        "splits": {"train": "dev", "test": "test"},
    }
    (datasets_dir / "tiny" / "manifest.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(ds, "DATASETS_DIR", datasets_dir)
    monkeypatch.setattr(ds, "CACHE_DIR", cache_dir)


def test_beir_loader_maps_train_to_dev_and_keeps_test_held_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_tiny_beir(tmp_path, monkeypatch)
    dataset = load_dataset("tiny", download=False)
    assert dataset.format == "beir" and len(dataset.records) == 2
    assert [(c.id, c.split) for c in dataset.cases] == [("dev-1", "dev"), ("test-2", "test")]
    # Document-level labels; judgements pointing at documents absent from the corpus are dropped.
    assert dataset.cases_for("test")[0].relevant == (RelevanceLabel("d2", None),)


def test_beir_archive_with_wrong_checksum_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_tiny_beir(tmp_path, monkeypatch, sha="0" * 64)
    with pytest.raises(DatasetError, match="checksum mismatch"):
        load_dataset("tiny", download=False)


def test_answer_subset_is_deterministic_and_seeded() -> None:
    cases = [
        EvalCase(str(i), "single_doc", "q", (RelevanceLabel("d"),), True, (), (), "")
        for i in range(100)
    ]
    first, again = answer_subset(cases, 10, seed=13), answer_subset(cases, 10, seed=13)
    assert first == again and len(first) == 10
    assert answer_subset(cases, 10, seed=14) != first
    assert answer_subset(cases, None, seed=13) == {c.id for c in cases}


def test_judge_parsing_is_strict_and_aligned() -> None:
    raw = json.dumps(
        {
            "verdicts": [
                {"claim": 2, "verdict": "Partial"},
                {"claim": 1, "verdict": "supported"},
                {"claim": 9, "verdict": "supported"},
                {"claim": 3, "verdict": "maybe"},
            ]
        }
    )
    assert parse_verdicts(raw, 3) == ["supported", "partial", None]
    assert parse_verdicts("not json", 2) == [None, None]
    assert parse_verdicts(
        "```json\n" + json.dumps({"verdicts": [{"claim": 1, "verdict": "unsupported"}]}) + "\n```",
        1,
    ) == ["unsupported"]
    # Unwrapped single verdicts and bare lists are accepted; unknown labels still are not.
    assert parse_verdicts(json.dumps({"claim": 1, "verdict": "supported", "reason": "x"}), 1) == [
        "supported"
    ]
    assert parse_verdicts(json.dumps([{"claim": 2, "verdict": "partial"}]), 2) == [None, "partial"]
    assert parse_verdicts(json.dumps({"claim": 1, "verdict": "probably"}), 1) == [None]


def test_claims_use_full_passage_text_not_snippets() -> None:
    full = "Hotel stays are reimbursed up to 180 EUR. The cap is 250 EUR per night in London. " * 5
    state = {
        "citations": [{"marker": "S1", "chunk_id": "c1", "snippet": full[:40]}],
        "evidence": [{"chunk_id": "c1", "text": full}],
        "claims": [
            {"text": "London is 250 EUR.", "supported": True, "markers": ["S1"]},
            {"text": "General advice.", "supported": False, "markers": []},
        ],
    }
    [claim] = claims_from_answer(state)  # uncited sentences are not judged
    assert claim.passages == [full]


class _ScriptedJudgeModel:
    name, model = "gemini", "judge-model"

    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)
        self.calls: list[list[ChatMessage]] = []

    async def complete(self, messages: list[ChatMessage], **kwargs: object) -> ChatResult:
        self.calls.append(messages)
        return ChatResult(self.replies.pop(0), self.name, self.model, Usage(50, 10))

    def stream(self, *args: object, **kwargs: object):  # type: ignore[no-untyped-def]  # pragma: no cover
        raise NotImplementedError


async def test_judge_batches_claims_and_scores_supported_share() -> None:
    model = _ScriptedJudgeModel(
        [
            json.dumps(
                {
                    "verdicts": [
                        {"claim": 1, "verdict": "supported"},
                        {"claim": 2, "verdict": "unsupported"},
                    ]
                }
            ),
            json.dumps({"verdicts": [{"claim": 1, "verdict": "partial"}]}),
        ]
    )
    judge = FaithfulnessJudge(model, max_claims_per_call=2)
    result = await judge.judge([ClaimToJudge(f"claim {i}", ["passage"]) for i in range(3)])
    assert len(model.calls) == 2
    assert "untrusted data" in model.calls[0][0].content
    scores = result.scores()
    assert scores["faithfulness_judged"] == pytest.approx(1 / 3)
    assert scores["judge_partial_rate"] == pytest.approx(1 / 3)
    assert scores["judged_claims"] == 3


def test_paired_bootstrap_detects_consistent_and_null_differences() -> None:
    better = [0.9, 0.8, 1.0, 0.7, 0.95, 0.85] * 10
    base = [v - 0.1 for v in better]
    diff, low, high = paired_bootstrap(better, base)
    assert diff == pytest.approx(0.1) and low > 0
    noise = [0.5, 0.6, 0.4, 0.55, 0.45, 0.5] * 10
    diff, low, high = paired_bootstrap(noise, list(reversed(noise)))
    assert low < 0 < high  # no significant difference


def test_mean_sd_over_runs() -> None:
    m, sd = mean_sd([0.9, 0.9, 0.9])
    assert m == pytest.approx(0.9) and sd == 0.0
    m, sd = mean_sd([0.8, 0.9, 1.0])
    assert m == pytest.approx(0.9) and sd == pytest.approx(0.1)
    assert mean_sd([None, None]) == (None, None)


def test_document_level_ranking_collapses_chunks_to_their_best_position() -> None:
    chunk_document = {"c1": "A", "c2": "B", "c3": "A", "c4": "C", "c5": "B"}
    assert rank_documents(["c3", "c2", "c1", "missing", "c4", "c5"], chunk_document) == [
        "A",
        "B",
        "C",
    ]
    pool = document_pool(RetrievalConfig(top_k=10, candidate_k=24))
    assert pool.top_k == pool.candidate_k == 40
    assert document_pool(RetrievalConfig(top_k=5, candidate_k=24)).top_k == 24


def test_judge_outage_is_reported_as_coverage_not_as_answer_error() -> None:
    cases = [
        EvalCase(f"c{i}", "single_doc", "q", (RelevanceLabel("d"),), True, (), (), "")
        for i in range(2)
    ]

    def result(case_id: str, extra: dict[str, object]) -> dict[str, object]:
        return {
            "case_id": case_id,
            "retrieval_metrics": {"recall@k": 1.0},
            "answer_metrics": {"correctness": 1.0, "judge_eligible": True, **extra},
            "latency_ms": 10.0,
            "retrieval_latency_ms": 5.0,
            "error": None,
        }

    results = [
        result("c0", {"judged_claims": 2, "faithfulness_judged": 1.0}),
        result("c1", {"judge_error": "ProviderQuotaExceededError"}),
    ]
    metrics = aggregate(results, cases, True, judge="nvidia/judge")
    assert metrics["error_rate"] == 0.0
    assert metrics["answer"]["correctness"] == 1.0
    assert metrics["answer"]["faithfulness_judged"] == 1.0
    assert metrics["answer"]["judge_coverage"] == {
        "eligible_cases": 2,
        "judged_cases": 1,
        "failed_cases": 1,
    }
