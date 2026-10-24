"""Operational commands.

python -m app.cli migrate        # Alembic upgrade head + LangGraph checkpoint tables
python -m app.cli seed-demo      # demo workspace + sample documents (see app/demo.py)
python -m app.cli evaluate ...   # benchmark (see app/evaluation/cli.py)
"""

import argparse
import asyncio
import sys
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger

BACKEND_DIR = Path(__file__).resolve().parents[1]
logger = get_logger("atlas.cli")


def _run[T](coro: Coroutine[Any, Any, T]) -> T:
    # psycopg's async mode cannot use the Proactor loop Windows selects by default.
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    return asyncio.run(coro, loop_factory=loop_factory)


async def _setup_checkpointer() -> None:
    from app.agent.runtime import create_postgres_runtime  # noqa: PLC0415

    runtime = await create_postgres_runtime(get_settings(), setup=True)
    await runtime.close()


def migrate() -> None:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    command.upgrade(config, "head")
    _run(_setup_checkpointer())
    logger.info("migrations_complete")


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["evaluate"]:
        # The benchmark has its own option parser; hand it everything after "evaluate".
        from app.evaluation.cli import main as evaluate_main  # noqa: PLC0415

        configure_logging(get_settings().log_level, json_logs=get_settings().log_json)
        evaluate_main(argv[1:])
        return
    settings = get_settings()
    configure_logging(settings.log_level, json_logs=settings.log_json)
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate", help="apply database migrations and checkpoint tables")
    seed = sub.add_parser("seed-demo", help="create a demo workspace with sample documents")
    seed.add_argument("--email", default="demo@atlas.example")
    seed.add_argument("--password", required=True)
    sub.add_parser("evaluate", help="run the retrieval/answer benchmark (see --help)")
    args = parser.parse_args(argv)

    if args.command == "migrate":
        migrate()
    elif args.command == "seed-demo":
        from app.demo import seed_demo  # noqa: PLC0415

        _run(seed_demo(settings, email=args.email, password=args.password))


if __name__ == "__main__":
    main()
