"""Evaluation via the API with mock providers: corpus prep, labels, metrics, storage."""

from collections.abc import Callable

import pytest

from app.core.config import Settings
from tests.integration.conftest import register, running_app

pytestmark = pytest.mark.integration


async def test_evaluation_run_produces_metrics_for_each_mode(
    api_settings: Callable[..., Settings],
) -> None:
    settings = api_settings(
        reranker_provider="mock", reranker_model="term-coverage", rate_limit_evaluation_per_hour=10
    )
    async with running_app(settings) as (_, client):
        owner = await register(client)
        datasets = (await client.get("/api/v1/evaluations/datasets", headers=owner.headers)).json()
        assert datasets[0]["name"] == "atlas-handbook-v1" and datasets[0]["case_count"] == 60

        started = await client.post(
            "/api/v1/evaluations/run",
            json={"modes": ["lexical", "hybrid_rerank"], "top_k": 5},
            headers=owner.headers,
        )
        assert started.status_code == 202, started.text
        run_ids = [r["id"] for r in started.json()["runs"]]
        assert len(run_ids) == 2

        listing = (await client.get("/api/v1/evaluations", headers=owner.headers)).json()
        assert {r["id"] for r in listing["items"]} >= set(run_ids)
        for run_id in run_ids:
            detail = (
                await client.get(f"/api/v1/evaluations/{run_id}", headers=owner.headers)
            ).json()
            assert detail["status"] == "succeeded", detail["error_message"]
            metrics = detail["metrics"]
            assert metrics["case_count"] == 60
            for key in ("recall@k", "precision@k", "hit_rate@k", "mrr", "ndcg@k"):
                assert 0.0 <= metrics["retrieval"][key] <= 1.0
            assert (
                metrics["retrieval"]["recall@k"] > 0.5
            )  # lexical search finds most labelled passages
            assert metrics["latency_ms"]["retrieval_p95"] is not None  # 60 samples ≥ 20
            assert "unanswerable" in metrics["by_category"]
            assert len(detail["cases"]) == 60
            assert detail["cases"][0]["question"]
        assert detail["config"]["reranker"] == "mock/term-coverage"

        # Evaluation data is tenant-scoped like everything else.
        other = await register(client)
        assert (
            await client.get(f"/api/v1/evaluations/{run_ids[0]}", headers=other.headers)
        ).status_code == 404
        assert (await client.get("/api/v1/evaluations", headers=other.headers)).json()["total"] == 0


async def test_evaluation_validation(api_settings: Callable[..., Settings]) -> None:
    async with running_app(api_settings()) as (_, client):
        owner = await register(client)
        assert (
            await client.post(
                "/api/v1/evaluations/run", json={"dataset": "nope"}, headers=owner.headers
            )
        ).status_code == 400
        # No reranker configured in these settings.
        assert (
            await client.post(
                "/api/v1/evaluations/run", json={"modes": ["hybrid_rerank"]}, headers=owner.headers
            )
        ).status_code == 400
