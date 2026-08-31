"""Tests for the incremental indexing git hooks.

`scripts/hooks/post-merge` and `scripts/hooks/post-checkout` push only the
files a pull / branch switch actually changed, instead of walking the repo.
These tests drive the real shell hooks against a real temp git repo and a
stub HTTP server standing in for the mnemos API.
"""
from __future__ import annotations

import json
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).parent.parent / "scripts" / "hooks"
NULL_SHA = "0" * 40


# ---------------------------------------------------------------------------
# Stub mnemos server
# ---------------------------------------------------------------------------


class _Recorder(BaseHTTPRequestHandler):
    calls: list[dict] = []

    def _record(self, method: str) -> None:
        length = int(self.headers.get("content-length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw) if raw else None
        except ValueError:
            body = {"_raw": raw.decode("utf-8", "replace")}
        type(self).calls.append({"method": method, "path": self.path, "body": body})
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"status":"ok"}')

    def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler API
        self._record("POST")

    def do_DELETE(self):  # noqa: N802 - BaseHTTPRequestHandler API
        self._record("DELETE")

    def log_message(self, format, *args):  # noqa: A002 - BaseHTTPRequestHandler API
        pass  # silence the default stderr logging


@pytest.fixture
def stub_server():
    _Recorder.calls = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Recorder)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, _Recorder
    server.shutdown()
    server.server_close()


def wait_for_calls(recorder, count: int, timeout: float = 10.0) -> list[dict]:
    """Hooks fire their HTTP calls detached, so poll until they land."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if len(recorder.calls) >= count:
            break
        time.sleep(0.05)
    # Give any straggler request a moment so over-sending is caught too.
    time.sleep(0.3)
    return list(recorder.calls)


# ---------------------------------------------------------------------------
# Temp git repo under a fake codebase root
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    """A git repo at <codebase_root>/Projects/acme/app, plus its fake HOME."""
    codebase_root = tmp_path / "codebase"
    repo_path = codebase_root / "Projects" / "acme" / "app"
    repo_path.mkdir(parents=True)

    home = tmp_path / "home"
    home.mkdir()

    _git(repo_path, "init", "-q", "-b", "main")
    _git(repo_path, "config", "user.email", "test@example.invalid")
    _git(repo_path, "config", "user.name", "Test")
    # Never let the developer's real global core.hooksPath run during tests.
    _git(repo_path, "config", "core.hooksPath", str(tmp_path / "no-hooks"))

    (repo_path / "main.go").write_text("package main\n")
    _git(repo_path, "add", "-A")
    _git(repo_path, "commit", "-q", "-m", "initial")

    return {
        "path": repo_path,
        "codebase_root": codebase_root,
        "home": home,
        "repos_config": home / "repos",
        "index_repos_config": home / "index-repos",
    }


def watch(repo: dict, *paths: Path) -> None:
    """Populate the memory-extraction list (and, by fallback, indexing)."""
    repo["repos_config"].write_text(
        "".join(f"{p}\n" for p in (paths or (repo["path"],)))
    )


def watch_index(repo: dict, *paths: Path) -> None:
    """Populate the indexing list, which overrides the fallback when present."""
    repo["index_repos_config"].write_text(
        "".join(f"{p}\n" for p in (paths or (repo["path"],)))
    )


def run_hook(repo: dict, server, name: str, *args: str, env_extra: dict | None = None):
    host, port = server.server_address
    env = {
        "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
        "HOME": str(repo["home"]),
        "MNEMOS_URL": f"http://{host}:{port}",
        "MNEMOS_REPOS_CONFIG": str(repo["repos_config"]),
        "MNEMOS_INDEX_REPOS_CONFIG": str(repo["index_repos_config"]),
        "MNEMOS_CODEBASE_ROOT": str(repo["codebase_root"]),
    }
    env.update(env_extra or {})
    return subprocess.run(
        ["sh", str(HOOKS_DIR / name), *args],
        cwd=repo["path"],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def commit(repo: dict, changes: dict[str, str | None], message: str = "change") -> str:
    """Apply {relative_path: content or None-to-delete} and commit. Returns sha."""
    for rel, content in changes.items():
        target = repo["path"] / rel
        if content is None:
            target.unlink()
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
    _git(repo["path"], "add", "-A")
    _git(repo["path"], "commit", "-q", "-m", message)
    return _git(repo["path"], "rev-parse", "HEAD")


# ---------------------------------------------------------------------------
# Path mapping
# ---------------------------------------------------------------------------


def _container_path(codebase_root: str, host_path: str) -> subprocess.CompletedProcess:
    script = (
        f'MNEMOS_CODEBASE_ROOT="{codebase_root}"; '
        f'. "{HOOKS_DIR}/mnemos-common.sh"; '
        f'mnemos_container_path "{host_path}"'
    )
    return subprocess.run(["sh", "-c", script], capture_output=True, text=True)


def test_container_path_maps_below_codebase_root():
    res = _container_path("/host/dev", "/host/dev/Projects/acme/app/main.go")
    assert res.returncode == 0
    assert res.stdout.strip() == "/data/codebase/Projects/acme/app/main.go"


def test_container_path_tolerates_trailing_slash_on_root():
    res = _container_path("/host/dev/", "/host/dev/Projects/acme/app/main.go")
    assert res.stdout.strip() == "/data/codebase/Projects/acme/app/main.go"


def test_container_path_rejects_path_outside_codebase_root():
    res = _container_path("/host/dev", "/elsewhere/repo/main.go")
    assert res.returncode != 0
    assert res.stdout.strip() == ""


def test_container_path_rejects_sibling_prefix_collision():
    """/host/dev-scratch must not be mistaken for a child of /host/dev."""
    res = _container_path("/host/dev", "/host/dev-scratch/repo/main.go")
    assert res.returncode != 0


# ---------------------------------------------------------------------------
# post-merge
# ---------------------------------------------------------------------------


def test_post_merge_indexes_changed_files(repo, stub_server):
    server, recorder = stub_server
    watch(repo)

    base = _git(repo["path"], "rev-parse", "HEAD")
    commit(repo, {"pkg/svc.go": "package pkg\n"})
    _git(repo["path"], "update-ref", "ORIG_HEAD", base)

    assert run_hook(repo, server, "post-merge").returncode == 0

    calls = wait_for_calls(recorder, 1)
    assert len(calls) == 1
    assert calls[0]["method"] == "POST"
    assert calls[0]["path"] == "/api/index"
    assert calls[0]["body"]["collection"] == "mnemos_code"
    assert calls[0]["body"]["file_path"] == "/data/codebase/Projects/acme/app/pkg/svc.go"
    assert calls[0]["body"]["content"] == "package pkg\n"
    # Tags are resolved server-side from config/projects.yaml.
    assert "tags" not in calls[0]["body"]


def test_post_merge_deletes_removed_files(repo, stub_server):
    server, recorder = stub_server
    watch(repo)

    base = _git(repo["path"], "rev-parse", "HEAD")
    commit(repo, {"main.go": None})
    _git(repo["path"], "update-ref", "ORIG_HEAD", base)

    run_hook(repo, server, "post-merge")

    calls = wait_for_calls(recorder, 1)
    assert len(calls) == 1
    assert calls[0]["method"] == "DELETE"
    assert calls[0]["path"] == "/api/index/mnemos_code/data/codebase/Projects/acme/app/main.go"


def test_post_merge_skips_unwatched_repo(repo, stub_server):
    server, recorder = stub_server
    watch(repo, repo["codebase_root"] / "Projects" / "other")

    base = _git(repo["path"], "rev-parse", "HEAD")
    commit(repo, {"pkg/svc.go": "package pkg\n"})
    _git(repo["path"], "update-ref", "ORIG_HEAD", base)

    run_hook(repo, server, "post-merge")

    assert wait_for_calls(recorder, 1, timeout=1.5) == []


def test_post_merge_is_a_noop_without_orig_head(repo, stub_server):
    server, recorder = stub_server
    watch(repo)
    commit(repo, {"pkg/svc.go": "package pkg\n"})

    assert run_hook(repo, server, "post-merge").returncode == 0
    assert wait_for_calls(recorder, 1, timeout=1.5) == []


def test_post_merge_falls_back_to_bulk_reindex_above_threshold(repo, stub_server):
    server, recorder = stub_server
    watch(repo)

    base = _git(repo["path"], "rev-parse", "HEAD")
    commit(repo, {f"pkg/f{i}.go": f"package pkg // {i}\n" for i in range(6)})
    _git(repo["path"], "update-ref", "ORIG_HEAD", base)

    run_hook(repo, server, "post-merge", env_extra={"MNEMOS_MAX_INCREMENTAL_FILES": "3"})

    calls = wait_for_calls(recorder, 1)
    assert len(calls) == 1, "one bulk call must replace the per-file pushes"
    assert calls[0]["path"] == "/api/reindex"
    assert calls[0]["body"] == {
        "collection": "mnemos_code",
        "path": "/data/codebase/Projects/acme/app",
        "full": True,
        "workers": 4,
    }


def test_post_merge_skips_oversized_files(repo, stub_server):
    server, recorder = stub_server
    watch(repo)

    base = _git(repo["path"], "rev-parse", "HEAD")
    commit(repo, {"big.json": "x" * 4096, "small.go": "package pkg\n"})
    _git(repo["path"], "update-ref", "ORIG_HEAD", base)

    run_hook(repo, server, "post-merge", env_extra={"MNEMOS_MAX_FILE_BYTES": "1024"})

    calls = wait_for_calls(recorder, 1)
    pushed = [c["body"]["file_path"] for c in calls]
    assert pushed == ["/data/codebase/Projects/acme/app/small.go"]


def test_post_merge_skips_binary_files(repo, stub_server):
    server, recorder = stub_server
    watch(repo)

    base = _git(repo["path"], "rev-parse", "HEAD")
    (repo["path"] / "logo.bin").write_bytes(b"\x00\x01\x02\xff binary \x00")
    (repo["path"] / "ok.go").write_text("package pkg\n")
    _git(repo["path"], "add", "-A")
    _git(repo["path"], "commit", "-q", "-m", "binary")
    _git(repo["path"], "update-ref", "ORIG_HEAD", base)

    run_hook(repo, server, "post-merge")

    calls = wait_for_calls(recorder, 1)
    pushed = [c["body"]["file_path"] for c in calls]
    assert pushed == ["/data/codebase/Projects/acme/app/ok.go"]


def test_post_merge_skips_repo_outside_codebase_root(repo, stub_server, tmp_path):
    server, recorder = stub_server
    watch(repo)

    base = _git(repo["path"], "rev-parse", "HEAD")
    commit(repo, {"pkg/svc.go": "package pkg\n"})
    _git(repo["path"], "update-ref", "ORIG_HEAD", base)

    run_hook(
        repo, server, "post-merge",
        env_extra={"MNEMOS_CODEBASE_ROOT": str(tmp_path / "somewhere-else")},
    )

    assert wait_for_calls(recorder, 1, timeout=1.5) == []


# ---------------------------------------------------------------------------
# post-checkout
# ---------------------------------------------------------------------------


def test_post_checkout_indexes_branch_switch_delta(repo, stub_server):
    server, recorder = stub_server
    watch(repo)

    prev = _git(repo["path"], "rev-parse", "HEAD")
    new = commit(repo, {"pkg/svc.go": "package pkg\n"})

    assert run_hook(repo, server, "post-checkout", prev, new, "1").returncode == 0

    calls = wait_for_calls(recorder, 1)
    assert len(calls) == 1
    assert calls[0]["body"]["file_path"] == "/data/codebase/Projects/acme/app/pkg/svc.go"


def test_post_checkout_ignores_file_checkout(repo, stub_server):
    server, recorder = stub_server
    watch(repo)

    prev = _git(repo["path"], "rev-parse", "HEAD")
    new = commit(repo, {"pkg/svc.go": "package pkg\n"})

    # $3 == 0 means a file checkout, which changes nothing repo-wide.
    run_hook(repo, server, "post-checkout", prev, new, "0")

    assert wait_for_calls(recorder, 1, timeout=1.5) == []


def test_post_checkout_bulk_reindexes_a_fresh_clone(repo, stub_server):
    server, recorder = stub_server
    watch(repo)

    head = _git(repo["path"], "rev-parse", "HEAD")
    run_hook(repo, server, "post-checkout", NULL_SHA, head, "1")

    calls = wait_for_calls(recorder, 1)
    assert len(calls) == 1
    assert calls[0]["path"] == "/api/reindex"
    assert calls[0]["body"]["path"] == "/data/codebase/Projects/acme/app"


def test_post_checkout_is_a_noop_when_head_is_unchanged(repo, stub_server):
    server, recorder = stub_server
    watch(repo)

    head = _git(repo["path"], "rev-parse", "HEAD")
    run_hook(repo, server, "post-checkout", head, head, "1")

    assert wait_for_calls(recorder, 1, timeout=1.5) == []


# ---------------------------------------------------------------------------
# Fail-safe behaviour
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "hook,args",
    [("post-merge", ()), ("post-checkout", ("a" * 40, "b" * 40, "1"))],
)
def test_hooks_never_fail_git_when_server_is_down(repo, stub_server, hook, args):
    server, _ = stub_server
    watch(repo)
    base = _git(repo["path"], "rev-parse", "HEAD")
    commit(repo, {"pkg/svc.go": "package pkg\n"})
    _git(repo["path"], "update-ref", "ORIG_HEAD", base)

    res = run_hook(
        repo, server, hook, *args,
        env_extra={"MNEMOS_URL": "http://127.0.0.1:1"},
    )
    assert res.returncode == 0


# ---------------------------------------------------------------------------
# Indexing list vs memory-extraction list
# ---------------------------------------------------------------------------


def _stage_pull(repo: dict) -> None:
    base = _git(repo["path"], "rev-parse", "HEAD")
    commit(repo, {"pkg/svc.go": "package pkg\n"})
    _git(repo["path"], "update-ref", "ORIG_HEAD", base)


def test_indexing_uses_its_own_list(repo, stub_server):
    server, recorder = stub_server
    # Not watched for memory, but explicitly listed for indexing.
    watch(repo, repo["codebase_root"] / "Projects" / "other")
    watch_index(repo)
    _stage_pull(repo)

    run_hook(repo, server, "post-merge")

    calls = wait_for_calls(recorder, 1)
    assert len(calls) == 1
    assert calls[0]["path"] == "/api/index"


def test_indexing_list_takes_precedence_over_memory_list(repo, stub_server):
    server, recorder = stub_server
    # Watched for memory, but deliberately absent from the indexing list:
    # the fallback must not resurrect it.
    watch(repo)
    watch_index(repo, repo["codebase_root"] / "Projects" / "other")
    _stage_pull(repo)

    run_hook(repo, server, "post-merge")

    assert wait_for_calls(recorder, 1, timeout=1.5) == []


def test_indexing_falls_back_to_memory_list_when_unset(repo, stub_server):
    server, recorder = stub_server
    watch(repo)
    assert not repo["index_repos_config"].exists()
    _stage_pull(repo)

    run_hook(repo, server, "post-merge")

    calls = wait_for_calls(recorder, 1)
    assert len(calls) == 1


def test_memory_extraction_ignores_the_indexing_list(repo, stub_server):
    """post-commit must stay gated on `repos`, whatever `index-repos` says."""
    server, recorder = stub_server
    watch(repo, repo["codebase_root"] / "Projects" / "other")
    watch_index(repo)
    commit(repo, {"pkg/svc.go": "package pkg\n"})

    res = run_hook(
        repo, server, "post-commit",
        env_extra={"MNEMOS_HOOK_TRIGGER": "post-commit"},
    )

    assert res.returncode == 0
    assert wait_for_calls(recorder, 1, timeout=1.5) == []


def test_watch_list_entry_does_not_match_sibling_prefix(repo, stub_server):
    """A listed root of .../app must not capture the sibling .../app-legacy."""
    server, recorder = stub_server
    watch_index(repo, Path(str(repo["path"]) + "-legacy"))
    _stage_pull(repo)

    run_hook(repo, server, "post-merge")

    assert wait_for_calls(recorder, 1, timeout=1.5) == []


def test_watch_list_entry_matches_the_repo_root_itself(repo, stub_server):
    server, recorder = stub_server
    watch_index(repo, repo["path"])
    _stage_pull(repo)

    run_hook(repo, server, "post-merge")

    assert len(wait_for_calls(recorder, 1)) == 1


def test_watch_list_tolerates_trailing_slash_entries(repo, stub_server):
    server, recorder = stub_server
    repo["index_repos_config"].write_text(f"{repo['path']}/\n")
    _stage_pull(repo)

    run_hook(repo, server, "post-merge")

    assert len(wait_for_calls(recorder, 1)) == 1


# ---------------------------------------------------------------------------
# Machine-local settings file
# ---------------------------------------------------------------------------


def test_settings_file_supplies_the_codebase_root(repo, stub_server, tmp_path):
    """Git hooks fire from GUI clients with no shell profile, so the codebase
    root has to survive without MNEMOS_CODEBASE_ROOT being exported."""
    server, recorder = stub_server
    watch(repo)
    _stage_pull(repo)

    settings = tmp_path / "mnemos-config"
    settings.write_text(f'MNEMOS_CODEBASE_ROOT="{repo["codebase_root"]}"\n')

    host, port = server.server_address
    res = subprocess.run(
        ["sh", str(HOOKS_DIR / "post-merge")],
        cwd=repo["path"],
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
            "HOME": str(repo["home"]),
            "MNEMOS_URL": f"http://{host}:{port}",
            "MNEMOS_REPOS_CONFIG": str(repo["repos_config"]),
            "MNEMOS_CONFIG_FILE": str(settings),
            # Deliberately NOT setting MNEMOS_CODEBASE_ROOT.
        },
        capture_output=True, text=True, timeout=30,
    )

    assert res.returncode == 0
    calls = wait_for_calls(recorder, 1)
    assert len(calls) == 1
    assert calls[0]["body"]["file_path"] == "/data/codebase/Projects/acme/app/pkg/svc.go"


def test_env_overrides_the_settings_file(repo, stub_server, tmp_path):
    server, recorder = stub_server
    watch(repo)
    _stage_pull(repo)

    settings = tmp_path / "mnemos-config"
    settings.write_text('MNEMOS_CODEBASE_ROOT="/nowhere"\n')

    run_hook(repo, server, "post-merge", env_extra={"MNEMOS_CONFIG_FILE": str(settings)})

    calls = wait_for_calls(recorder, 1)
    assert len(calls) == 1
