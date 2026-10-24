"""Optional tracing integrations. The application never depends on either being configured.

- OpenTelemetry (``OTEL_ENABLED=true``; install ``uv sync --extra observability``): FastAPI
  request spans plus one span per agent graph node, exported over OTLP/HTTP to
  ``OTEL_EXPORTER_OTLP_ENDPOINT`` (standard OTel env vars apply).
- LangSmith (``LANGSMITH_TRACING=true`` + ``LANGSMITH_API_KEY``): LangGraph's built-in tracing.
  Privacy: LangSmith receives prompts, which include retrieved document text. It is off by
  default for that reason.

Structured JSON logs (always on) remain the primary, privacy-conscious local record.
"""

import os

from fastapi import FastAPI
from opentelemetry import trace

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger("atlas.observability")
tracer = trace.get_tracer("atlas.agent")


def configure_observability(app: FastAPI, settings: Settings) -> None:
    if settings.langsmith_tracing and settings.langsmith_api_key is not None:
        os.environ["LANGSMITH_TRACING"] = "true"
        os.environ["LANGSMITH_API_KEY"] = settings.langsmith_api_key.get_secret_value()
        os.environ["LANGSMITH_PROJECT"] = settings.langsmith_project
        logger.info("langsmith_enabled", project=settings.langsmith_project)
    else:
        # LangChain reads these from the environment; make "off" explicit.
        os.environ.setdefault("LANGSMITH_TRACING", "false")

    if not settings.otel_enabled:
        return
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (  # noqa: PLC0415
            OTLPSpanExporter,
        )
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor  # noqa: PLC0415
        from opentelemetry.sdk.resources import Resource  # noqa: PLC0415
        from opentelemetry.sdk.trace import TracerProvider  # noqa: PLC0415
        from opentelemetry.sdk.trace.export import BatchSpanProcessor  # noqa: PLC0415
    except ImportError:
        logger.warning("otel_unavailable", hint="uv sync --extra observability")
        return
    provider = TracerProvider(
        resource=Resource.create({"service.name": settings.otel_service_name})
    )
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    # Health probes would dominate traces on free hosts that ping them constantly.
    FastAPIInstrumentor.instrument_app(app, excluded_urls="health/live,health/ready")
    logger.info("otel_enabled", service=settings.otel_service_name)
