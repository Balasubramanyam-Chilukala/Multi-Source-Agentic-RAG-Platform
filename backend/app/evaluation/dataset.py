"""Loading version-controlled evaluation datasets from ``backend/eval/datasets/<name>/``."""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DATASETS_DIR = Path(__file__).resolve().parents[2] / "eval" / "datasets"
CATEGORIES = (
    "single_doc",
    "multi_chunk",
    "multi_doc",
    "paraphrase",
    "ambiguous",
    "unanswerable",
    "conflicting",
    "citation",
    "injection",
)


class DatasetError(ValueError):
    pass


@dataclass(frozen=True)
class RelevanceLabel:
    doc: str
    contains: str


@dataclass(frozen=True)
class EvalCase:
    id: str
    category: str
    question: str
    relevant: tuple[RelevanceLabel, ...]
    answerable: bool
    required_facts: tuple[tuple[str, ...], ...]
    forbidden_facts: tuple[str, ...]
    reference_answer: str
    expected_outcome: str | None = None


@dataclass
class Dataset:
    name: str
    version: str
    description: str
    root: Path
    cases: list[EvalCase]
    documents: list[Path] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for case in self.cases:
            counts[case.category] = counts.get(case.category, 0) + 1
        return {
            "name": self.name,
            "version": self.version,
            "case_count": len(self.cases),
            "categories": counts,
            "document_count": len(self.documents),
            "description": self.description,
        }


def _case(raw: dict[str, Any], documents: set[str]) -> EvalCase:
    try:
        case = EvalCase(
            id=str(raw["id"]),
            category=str(raw["category"]),
            question=str(raw["question"]),
            relevant=tuple(
                RelevanceLabel(r["doc"], r["contains"]) for r in raw.get("relevant", [])
            ),
            answerable=bool(raw["answerable"]),
            required_facts=tuple(tuple(f) for f in raw.get("required_facts", [])),
            forbidden_facts=tuple(raw.get("forbidden_facts", [])),
            reference_answer=str(raw.get("reference_answer", "")),
            expected_outcome=raw.get("expected_outcome"),
        )
    except (KeyError, TypeError) as exc:
        raise DatasetError(f"malformed case {raw.get('id', '?')}: {exc}") from exc
    if case.category not in CATEGORIES:
        raise DatasetError(f"case {case.id}: unknown category {case.category}")
    unknown = {label.doc for label in case.relevant} - documents
    if unknown:
        raise DatasetError(f"case {case.id}: labels reference unknown documents {sorted(unknown)}")
    if case.answerable and not case.relevant:
        raise DatasetError(f"case {case.id}: answerable cases need relevance labels")
    return case


def available_datasets() -> list[str]:
    if not DATASETS_DIR.exists():
        return []
    return sorted(p.name for p in DATASETS_DIR.iterdir() if (p / "manifest.json").exists())


def load_dataset(name: str) -> Dataset:
    if name not in available_datasets():
        raise DatasetError(f"unknown dataset {name!r}")
    root = DATASETS_DIR / name
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    documents = sorted(p for p in (root / "docs").iterdir() if p.is_file())
    names = {p.name for p in documents}
    cases = []
    for number, line in enumerate(
        (root / "dataset.jsonl").read_text(encoding="utf-8").splitlines(), start=1
    ):
        if line.strip():
            try:
                cases.append(_case(json.loads(line), names))
            except json.JSONDecodeError as exc:
                raise DatasetError(f"dataset.jsonl line {number} is not valid JSON") from exc
    ids = [c.id for c in cases]
    if len(ids) != len(set(ids)):
        raise DatasetError("duplicate case ids")
    return Dataset(
        manifest["name"],
        manifest["version"],
        manifest.get("description", ""),
        root,
        cases,
        documents,
    )
