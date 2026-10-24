"""Evaluation datasets from ``backend/eval/datasets/<name>/``.

Two formats:

- ``files`` (e.g. ``atlas-handbook-v1``): documents in ``docs/``, cases in ``dataset.jsonl``,
  phrase-level relevance labels, and an explicit ``split`` (``dev``/``test``) per case.
- ``beir`` (e.g. ``scifact``): a public BEIR-format corpus downloaded on first use, verified
  against a pinned SHA-256, and cached under ``eval/.cache/`` (never committed). BEIR's
  ``train`` qrels become the ``dev`` split (for any tuning) and ``test`` stays held out.

Tuning (thresholds, prompts) may only look at ``dev``; reported numbers come from ``test``.
"""

import csv
import hashlib
import io
import json
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

EVAL_DIR = Path(__file__).resolve().parents[2] / "eval"
DATASETS_DIR = EVAL_DIR / "datasets"
CACHE_DIR = EVAL_DIR / ".cache"
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
    "scientific_claim",
)
SPLITS = ("dev", "test")


class DatasetError(ValueError):
    pass


@dataclass(frozen=True)
class RelevanceLabel:
    doc: str
    # Phrase that must appear in the relevant chunk; None = every chunk of the document.
    contains: str | None = None


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
    split: str = "test"


@dataclass(frozen=True)
class DocRecord:
    """A corpus document for ``beir`` datasets (``files`` datasets use real files)."""

    external_id: str
    title: str
    text: str


@dataclass
class Dataset:
    name: str
    version: str
    description: str
    root: Path
    cases: list[EvalCase]
    documents: list[Path] = field(default_factory=list)
    records: list[DocRecord] = field(default_factory=list)
    format: str = "files"
    license: str = ""
    source: str = ""

    def cases_for(self, split: str) -> list[EvalCase]:
        if split == "all":
            return list(self.cases)
        if split not in SPLITS:
            raise DatasetError(f"unknown split {split!r}; use dev, test or all")
        return [c for c in self.cases if c.split == split]

    def summary(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for case in self.cases:
            counts[case.category] = counts.get(case.category, 0) + 1
        splits = {s: sum(1 for c in self.cases if c.split == s) for s in SPLITS}
        return {
            "name": self.name,
            "version": self.version,
            "case_count": len(self.cases),
            "categories": counts,
            "splits": splits,
            "document_count": len(self.documents) or len(self.records),
            "description": self.description,
            "format": self.format,
            "license": self.license,
            "source": self.source,
        }


def _case(raw: dict[str, Any], documents: set[str]) -> EvalCase:
    try:
        case = EvalCase(
            id=str(raw["id"]),
            category=str(raw["category"]),
            question=str(raw["question"]),
            relevant=tuple(
                RelevanceLabel(r["doc"], r.get("contains")) for r in raw.get("relevant", [])
            ),
            answerable=bool(raw["answerable"]),
            required_facts=tuple(tuple(f) for f in raw.get("required_facts", [])),
            forbidden_facts=tuple(raw.get("forbidden_facts", [])),
            reference_answer=str(raw.get("reference_answer", "")),
            expected_outcome=raw.get("expected_outcome"),
            split=str(raw.get("split", "test")),
        )
    except (KeyError, TypeError) as exc:
        raise DatasetError(f"malformed case {raw.get('id', '?')}: {exc}") from exc
    if case.category not in CATEGORIES:
        raise DatasetError(f"case {case.id}: unknown category {case.category}")
    if case.split not in SPLITS:
        raise DatasetError(f"case {case.id}: unknown split {case.split}")
    unknown = {label.doc for label in case.relevant} - documents
    if unknown:
        raise DatasetError(f"case {case.id}: labels reference unknown documents {sorted(unknown)}")
    if case.answerable and not case.relevant:
        raise DatasetError(f"case {case.id}: answerable cases need relevance labels")
    return case


def dataset_summary(name: str) -> dict[str, Any]:
    """Summary for listings. Never downloads: an uncached public corpus is described from the
    counts pinned in its manifest."""
    manifest = json.loads((DATASETS_DIR / name / "manifest.json").read_text(encoding="utf-8"))
    if (
        manifest.get("format") == "beir"
        and not (CACHE_DIR / name / Path(manifest["archive_url"]).name).exists()
    ):
        splits = manifest.get("case_counts", {})
        return {
            "name": manifest["name"],
            "version": manifest["version"],
            "case_count": sum(splits.values()),
            "categories": {manifest["category"]: sum(splits.values())},
            "splits": splits,
            "document_count": manifest.get("document_count", 0),
            "description": manifest.get("description", ""),
            "format": "beir",
            "license": manifest.get("license", ""),
            "source": manifest.get("source", ""),
        }
    return load_dataset(name, download=False).summary()


def available_datasets() -> list[str]:
    if not DATASETS_DIR.exists():
        return []
    return sorted(p.name for p in DATASETS_DIR.iterdir() if (p / "manifest.json").exists())


def load_dataset(name: str, *, download: bool = True) -> Dataset:
    if name not in available_datasets():
        raise DatasetError(f"unknown dataset {name!r}")
    root = DATASETS_DIR / name
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("format", "files") == "beir":
        return _load_beir(root, manifest, download=download)
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
        license=manifest.get("license", ""),
        source=manifest.get("source", ""),
    )


def _fetch_archive(manifest: dict[str, Any], download: bool) -> Path:
    """Download (once) and verify the pinned archive. The archive is treated as data: it is
    parsed as JSONL/TSV from memory and never extracted or executed."""
    target: Path = CACHE_DIR / str(manifest["name"]) / Path(str(manifest["archive_url"])).name
    if not target.exists():
        if not download:
            raise DatasetError(
                f"{manifest['name']} is not downloaded; run the benchmark once online"
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        with httpx.stream(
            "GET", manifest["archive_url"], timeout=120, follow_redirects=True
        ) as response:
            response.raise_for_status()
            partial = target.with_suffix(".partial")
            with partial.open("wb") as handle:
                for piece in response.iter_bytes():
                    handle.write(piece)
        partial.replace(target)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    if digest != manifest["sha256"]:
        target.unlink(missing_ok=True)
        raise DatasetError(
            f"{manifest['name']} archive checksum mismatch ({digest}); refusing to use it"
        )
    return target


def _load_beir(root: Path, manifest: dict[str, Any], *, download: bool) -> Dataset:
    archive = zipfile.ZipFile(_fetch_archive(manifest, download))
    prefix = manifest["archive_prefix"]

    def jsonl(member: str) -> list[dict[str, Any]]:
        return [
            json.loads(line)
            for line in archive.read(f"{prefix}/{member}").decode("utf-8").splitlines()
            if line.strip()
        ]

    records = [
        DocRecord(str(d["_id"]), str(d.get("title") or ""), str(d.get("text") or ""))
        for d in jsonl("corpus.jsonl")
    ]
    queries = {str(q["_id"]): str(q["text"]) for q in jsonl("queries.jsonl")}
    known = {r.external_id for r in records}
    cases: list[EvalCase] = []
    for source_split, split in manifest["splits"].items():
        rows = list(
            csv.reader(
                io.StringIO(archive.read(f"{prefix}/qrels/{source_split}.tsv").decode("utf-8")),
                delimiter="\t",
            )
        )
        relevant: dict[str, list[str]] = {}
        for query_id, doc_id, score in rows[1:]:  # skip header
            if int(score) > 0 and doc_id in known:
                relevant.setdefault(query_id, []).append(doc_id)
        for query_id in sorted(relevant, key=lambda q: (len(q), q)):
            cases.append(
                EvalCase(
                    id=f"{split}-{query_id}",
                    category=manifest["category"],
                    question=queries[query_id],
                    relevant=tuple(RelevanceLabel(doc) for doc in sorted(set(relevant[query_id]))),
                    answerable=True,
                    required_facts=(),
                    forbidden_facts=(),
                    reference_answer="",
                    split=split,
                )
            )
    return Dataset(
        manifest["name"],
        manifest["version"],
        manifest.get("description", ""),
        root,
        cases,
        records=records,
        format="beir",
        license=manifest.get("license", ""),
        source=manifest.get("source", ""),
    )
