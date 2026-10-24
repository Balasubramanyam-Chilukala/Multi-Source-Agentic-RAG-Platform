"""Evaluation endpoints. Runs execute in the background and are polled by the dashboard."""

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.deps import (
    AdminDep,
    PrincipalDep,
    ProvidersDep,
    SessionDep,
    SettingsDep,
    user_rate_limit,
)
from app.core.errors import BadRequestError, NotFoundError
from app.db.models.evaluation import EvaluationCaseResult, EvaluationMode, EvaluationRun
from app.db.models.knowledge import JobStatus
from app.evaluation.dataset import DatasetError, available_datasets, load_dataset
from app.evaluation.runner import ALL_MODES, EvalOptions, EvaluationRunner
from app.schemas.common import ORMModel, Page
from app.services.common import audit

router = APIRouter(prefix="/evaluations", tags=["evaluations"])


class EvaluationRequest(BaseModel):
    dataset: str = "atlas-handbook-v1"
    modes: list[EvaluationMode] | None = Field(
        default=None, description="default: every available mode"
    )
    include_answers: bool = Field(default=False, description="also run the agent (uses LLM quota)")
    top_k: int = Field(default=5, ge=1, le=20)


class EvaluationRunOut(ORMModel):
    id: uuid.UUID
    dataset_name: str
    dataset_version: str
    retrieval_mode: EvaluationMode
    status: JobStatus
    config: dict[str, Any]
    metrics: dict[str, Any]
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class EvaluationCaseOut(BaseModel):
    case_id: str
    category: str | None
    question: str | None
    retrieved_chunk_ids: list[str]
    retrieval_metrics: dict[str, Any]
    answer_metrics: dict[str, Any]
    answer: str | None
    latency_ms: float | None
    error_message: str | None


class EvaluationRunDetail(EvaluationRunOut):
    cases: list[EvaluationCaseOut]


class StartedEvaluation(BaseModel):
    runs: list[EvaluationRunOut]


class DatasetSummary(BaseModel):
    name: str
    version: str
    case_count: int
    categories: dict[str, int]
    document_count: int
    description: str


@router.get("/datasets", response_model=list[DatasetSummary])
async def list_datasets(principal: PrincipalDep) -> list[DatasetSummary]:
    return [DatasetSummary(**load_dataset(name).summary()) for name in available_datasets()]


@router.post(
    "/run",
    response_model=StartedEvaluation,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(user_rate_limit("evaluation", per="hour"))],
)
async def run_evaluation(
    body: EvaluationRequest,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    providers: ProvidersDep,
    principal: AdminDep,
    background: BackgroundTasks,
) -> StartedEvaluation:
    modes = (
        [m.value for m in body.modes]
        if body.modes
        else [m for m in ALL_MODES if m != "hybrid_rerank" or providers.reranker() is not None]
    )
    options = EvalOptions(
        dataset=body.dataset, modes=modes, include_answers=body.include_answers, top_k=body.top_k
    )
    runner = EvaluationRunner(
        request.app.state.db.sessionmaker, providers, settings, request.app.state.pipeline
    )
    try:
        run_ids = await runner.create_runs(principal.tenant_id, options)
    except DatasetError as exc:
        raise BadRequestError(str(exc)) from None
    audit(
        session,
        action="evaluation.run",
        principal=principal,
        resource_type="evaluation",
        request_id=request.state.request_id,
        details={"dataset": body.dataset, "modes": modes, "include_answers": body.include_answers},
    )
    await session.commit()
    background.add_task(runner.execute, principal.tenant_id, run_ids, options)
    runs = await session.scalars(select(EvaluationRun).where(EvaluationRun.id.in_(run_ids)))
    return StartedEvaluation(runs=[EvaluationRunOut.model_validate(r) for r in runs])


@router.get("", response_model=Page[EvaluationRunOut])
async def list_evaluations(
    session: SessionDep,
    principal: PrincipalDep,
    dataset: str | None = None,
    mode: EvaluationMode | None = None,
    status_filter: Annotated[JobStatus | None, Query(alias="status")] = None,
    since: Annotated[datetime | None, Query(description="only runs created at/after")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[EvaluationRunOut]:
    scope = [EvaluationRun.tenant_id == principal.tenant_id]
    if dataset:
        scope.append(EvaluationRun.dataset_name == dataset)
    if mode:
        scope.append(EvaluationRun.retrieval_mode == mode)
    if status_filter:
        scope.append(EvaluationRun.status == status_filter)
    if since:
        scope.append(EvaluationRun.created_at >= since)
    total = await session.scalar(select(func.count()).select_from(EvaluationRun).where(*scope))
    rows = await session.scalars(
        select(EvaluationRun)
        .where(*scope)
        .order_by(EvaluationRun.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return Page(
        items=[EvaluationRunOut.model_validate(r) for r in rows],
        total=total or 0,
        limit=limit,
        offset=offset,
    )


@router.get("/{evaluation_id}", response_model=EvaluationRunDetail)
async def get_evaluation(
    evaluation_id: uuid.UUID, session: SessionDep, principal: PrincipalDep
) -> EvaluationRunDetail:
    run = await session.scalar(
        select(EvaluationRun).where(
            EvaluationRun.id == evaluation_id, EvaluationRun.tenant_id == principal.tenant_id
        )
    )
    if run is None:
        raise NotFoundError("Evaluation run not found")
    try:
        questions = {c.id: c for c in load_dataset(run.dataset_name).cases}
    except DatasetError:
        questions = {}
    cases = await session.scalars(
        select(EvaluationCaseResult)
        .where(EvaluationCaseResult.evaluation_run_id == run.id)
        .order_by(EvaluationCaseResult.case_id)
    )
    base = EvaluationRunOut.model_validate(run)
    return EvaluationRunDetail(
        **base.model_dump(),
        cases=[
            EvaluationCaseOut(
                case_id=c.case_id,
                category=questions[c.case_id].category if c.case_id in questions else None,
                question=questions[c.case_id].question if c.case_id in questions else None,
                retrieved_chunk_ids=c.retrieved_chunk_ids,
                retrieval_metrics=c.retrieval_metrics,
                answer_metrics=c.answer_metrics,
                answer=c.answer,
                latency_ms=c.latency_ms,
                error_message=c.error_message,
            )
            for c in cases
        ],
    )
