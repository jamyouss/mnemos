from __future__ import annotations

from pathlib import Path

import yaml

from eval.harness.schema import GoldenCandidate, GoldenItem


def load_golden(path: Path) -> list[GoldenItem]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or []
    return [GoldenItem(**item) for item in raw]


def save_golden(path: Path, items: list[GoldenItem]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialised = [item.model_dump(exclude_defaults=False) for item in items]
    with path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(serialised, fh, sort_keys=False, allow_unicode=True)


def load_candidates(path: Path) -> list[GoldenCandidate]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or []
    return [GoldenCandidate(**c) for c in raw]


def save_candidates(path: Path, candidates: list[GoldenCandidate]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialised = [c.model_dump() for c in candidates]
    with path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(serialised, fh, sort_keys=False, allow_unicode=True)


def promote_candidates(
    candidates_path: Path,
    golden_path: Path,
) -> tuple[int, int]:
    """Move accepted candidates into the golden set. Returns (promoted, remaining)."""
    candidates = load_candidates(candidates_path)
    accepted = [c for c in candidates if c.reviewed and c.accepted]
    # Once a candidate has been reviewed it's consumed: accepted → golden, rejected → discarded.
    # Only un-reviewed candidates stay in the pool for later triage.
    remaining = [c for c in candidates if not c.reviewed]

    existing = load_golden(golden_path)
    existing_ids = {item.id for item in existing}

    new_items: list[GoldenItem] = []
    for candidate in accepted:
        if candidate.id in existing_ids:
            continue
        new_items.append(
            GoldenItem(
                id=candidate.id,
                query=candidate.query,
                intent=candidate.intent,
                expected_collections=[candidate.source_collection],
                expected_files=candidate.suggested_files,
                k_relevant=max(1, len(candidate.suggested_files)),
            )
        )

    if new_items:
        save_golden(golden_path, existing + new_items)
    save_candidates(candidates_path, remaining)

    return len(new_items), len(remaining)
