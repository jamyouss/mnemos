import json
import tempfile
from pathlib import Path

from rag_core.state import IndexState


def test_load_empty_state():
    with tempfile.TemporaryDirectory() as tmpdir:
        state = IndexState(state_dir=tmpdir)
        assert state.last_full_reindex is None
        assert state.last_incremental is None
        assert state.last_git_sha is None


def test_save_and_load_state():
    with tempfile.TemporaryDirectory() as tmpdir:
        state = IndexState(state_dir=tmpdir)
        state.last_full_reindex = "2026-03-12T10:00:00Z"
        state.last_git_sha = "abc123"
        state.save()

        state2 = IndexState(state_dir=tmpdir)
        assert state2.last_full_reindex == "2026-03-12T10:00:00Z"
        assert state2.last_git_sha == "abc123"


def test_update_incremental():
    with tempfile.TemporaryDirectory() as tmpdir:
        state = IndexState(state_dir=tmpdir)
        state.mark_incremental("def456")
        state.save()

        state2 = IndexState(state_dir=tmpdir)
        assert state2.last_incremental is not None
        assert state2.last_git_sha == "def456"
