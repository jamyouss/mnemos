from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


class IndexState:
    def __init__(self, state_dir: str) -> None:
        self._path = Path(state_dir) / "index_state.json"
        self.last_full_reindex: str | None = None
        self.last_incremental: str | None = None
        self.last_git_sha: str | None = None
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            data = json.loads(self._path.read_text())
            self.last_full_reindex = data.get("last_full_reindex")
            self.last_incremental = data.get("last_incremental")
            self.last_git_sha = data.get("last_git_sha")

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(
                {
                    "last_full_reindex": self.last_full_reindex,
                    "last_incremental": self.last_incremental,
                    "last_git_sha": self.last_git_sha,
                },
                indent=2,
            )
        )

    def mark_incremental(self, git_sha: str | None = None) -> None:
        self.last_incremental = datetime.now(timezone.utc).isoformat()
        if git_sha:
            self.last_git_sha = git_sha

    def mark_full_reindex(self) -> None:
        self.last_full_reindex = datetime.now(timezone.utc).isoformat()
