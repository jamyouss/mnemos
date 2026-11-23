"""Tests for deriving the git-hook repo lists from the server's projects config.

The projects config declares path prefixes, often subdirectories; a git hook
fires at a repository root. The derivation has to bridge that gap, and get the
edge cases right — otherwise a hook silently stops firing for a project.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

# The script is not importable by name (hyphens, and it lives in scripts/),
# so load it by path.
_MODULE_PATH = Path(__file__).parent.parent / "scripts" / "derive-hook-repos.py"
_spec = importlib.util.spec_from_file_location("derive_hook_repos", _MODULE_PATH)
assert _spec is not None and _spec.loader is not None
derive = importlib.util.module_from_spec(_spec)
sys.modules["derive_hook_repos"] = derive
_spec.loader.exec_module(derive)


def _repo(root: Path, *subdirs: str) -> Path:
    """Create a directory holding a .git marker, plus optional subdirs."""
    root.mkdir(parents=True, exist_ok=True)
    (root / ".git").mkdir(exist_ok=True)
    for sub in subdirs:
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root


def test_prefix_resolves_to_its_enclosing_repository(tmp_path):
    """A prefix pointing deep inside a repo must yield the repo root, since
    that is where the hook fires."""
    code = tmp_path / "code"
    _repo(code / "Projects/acme/app", "src/components")

    roots = derive.derive_roots(["Projects/acme/app/src/components/"], code)

    assert roots == [str(code / "Projects/acme/app")]


def test_sibling_repositories_are_both_returned(tmp_path):
    """A directory holding several repos is not itself a repo: each child has
    to be listed on its own."""
    code = tmp_path / "code"
    (code / "Projects/suite").mkdir(parents=True)
    _repo(code / "Projects/suite/webapp")
    _repo(code / "Projects/suite/backoffice")

    roots = derive.derive_roots(["Projects/suite/webapp/", "Projects/suite/backoffice/"], code)

    assert roots == [
        str(code / "Projects/suite/backoffice"),
        str(code / "Projects/suite/webapp"),
    ]


def test_nested_repository_is_dropped_when_a_parent_covers_it(tmp_path):
    """The hooks match on path prefix, so a child root would be dead weight."""
    code = tmp_path / "code"
    _repo(code / "Projects/acme")
    _repo(code / "Projects/acme/vendored")

    roots = derive.derive_roots(["Projects/acme/", "Projects/acme/vendored/"], code)

    assert roots == [str(code / "Projects/acme")]


def test_many_prefixes_in_one_repo_collapse_to_one_root(tmp_path):
    code = tmp_path / "code"
    _repo(code / "Projects/acme", "apps/a", "apps/b", "libs/c")

    roots = derive.derive_roots(
        ["Projects/acme/apps/a/", "Projects/acme/apps/b/", "Projects/acme/libs/c/"], code
    )

    assert roots == [str(code / "Projects/acme")]


def test_prefix_outside_any_repository_is_skipped(tmp_path):
    """No .git anywhere above it means no hook can ever fire for it."""
    code = tmp_path / "code"
    (code / "Projects/loose/src").mkdir(parents=True)

    assert derive.derive_roots(["Projects/loose/src/"], code) == []


def test_missing_directory_is_skipped(tmp_path):
    """A project entry outliving a deleted project must not break the run."""
    code = tmp_path / "code"
    _repo(code / "Projects/acme")

    roots = derive.derive_roots(["Projects/acme/", "Projects/gone/"], code)

    assert roots == [str(code / "Projects/acme")]


def test_walk_up_stops_at_the_codebase_root(tmp_path):
    """A .git above the mount root must not drag an outside repo in."""
    code = tmp_path / "code"
    _repo(tmp_path)                      # .git at the parent of the mount
    (code / "Projects/acme").mkdir(parents=True)

    assert derive.derive_roots(["Projects/acme/"], code) == []


def test_no_prefix_derives_nothing(tmp_path):
    code = tmp_path / "code"
    code.mkdir()
    assert derive.derive_roots([], code) == []


# ---------------------------------------------------------------------------
# main: projects from GET /api/projects, lists written to disk
# ---------------------------------------------------------------------------


class _ProjectsApi(BaseHTTPRequestHandler):
    """`GET /api/projects` answering `rows`, recording the auth header."""

    rows: list[dict] = []
    status = 200
    auth_headers: list[str | None] = []

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler API
        type(self).auth_headers.append(self.headers.get("Authorization"))
        if self.path != "/api/projects" or self.status != 200:
            self.send_response(404 if self.path != "/api/projects" else self.status)
            self.end_headers()
            return
        body = json.dumps({"revision": 1, "writable": True, "paths": self.rows, "problems": []}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):  # noqa: A002 - BaseHTTPRequestHandler API
        pass


@pytest.fixture
def projects_api(monkeypatch):
    _ProjectsApi.rows, _ProjectsApi.status, _ProjectsApi.auth_headers = [], 200, []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ProjectsApi)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("MNEMOS_API_KEY", "hooks-key")
    yield f"http://127.0.0.1:{server.server_address[1]}", _ProjectsApi
    server.shutdown()
    server.server_close()


def _row(prefix: str) -> dict:
    return {"prefix": prefix, "tags": ["tag"], "exclude_dirs": [], "exclude_exts": []}


def _run_main(monkeypatch, url: str, code: Path, out: Path) -> int:
    monkeypatch.setattr(sys, "argv", [
        "derive-hook-repos.py",
        "--mnemos-url", url,
        "--codebase-root", str(code),
        "--config-dir", str(out),
    ])
    return derive.main()


def test_written_files_are_marked_generated(tmp_path, monkeypatch, projects_api):
    """The installer refuses to clobber a hand-written list, and tells the two
    apart by this marker."""
    url, api = projects_api
    api.rows = [_row("Projects/acme/src/")]
    code = tmp_path / "code"
    _repo(code / "Projects/acme", "src")
    out = tmp_path / "cfgdir"

    assert _run_main(monkeypatch, url, code, out) == 0

    for name in ("repos", "index-repos"):
        text = (out / name).read_text()
        assert "# Generated by scripts/derive-hook-repos.py" in text
        assert str(code / "Projects/acme") in text
    assert api.auth_headers == ["Bearer hooks-key"]


def test_refuses_to_write_empty_lists(tmp_path, monkeypatch, projects_api):
    """Writing empty lists would silently switch every hook off."""
    url, _ = projects_api
    code = tmp_path / "code"
    code.mkdir()
    out = tmp_path / "cfgdir"

    assert _run_main(monkeypatch, url, code, out) == 1
    assert not out.exists()


def test_an_unreadable_projects_config_writes_nothing(tmp_path, monkeypatch, projects_api, capsys):
    url, api = projects_api
    api.status = 403
    code = tmp_path / "code"
    _repo(code / "Projects/acme")
    out = tmp_path / "cfgdir"

    assert _run_main(monkeypatch, url, code, out) == 2
    assert f"cannot read the projects from {url}/api/projects" in capsys.readouterr().err
    assert not out.exists()
