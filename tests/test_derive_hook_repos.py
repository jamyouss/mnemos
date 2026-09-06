"""Tests for deriving the git-hook repo lists from config/projects.yaml.

`paths:` declares path prefixes, often subdirectories; a git hook fires at a
repository root. The derivation has to bridge that gap, and get the edge
cases right — otherwise a hook silently stops firing for a project.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

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


def _projects(tmp_path: Path, prefixes: list[str]) -> Path:
    body = "paths:\n" + "".join(f"  {p}:\n    - tag\n" for p in prefixes)
    cfg = tmp_path / "projects.yaml"
    cfg.write_text(body, encoding="utf-8")
    return cfg


def test_prefix_resolves_to_its_enclosing_repository(tmp_path):
    """A prefix pointing deep inside a repo must yield the repo root, since
    that is where the hook fires."""
    code = tmp_path / "code"
    _repo(code / "Projects/acme/app", "src/components")
    cfg = _projects(tmp_path, ["Projects/acme/app/src/components/"])

    assert derive.derive_roots(cfg, code) == [str(code / "Projects/acme/app")]


def test_sibling_repositories_are_both_returned(tmp_path):
    """A directory holding several repos is not itself a repo: each child has
    to be listed on its own."""
    code = tmp_path / "code"
    (code / "Projects/suite").mkdir(parents=True)
    _repo(code / "Projects/suite/webapp")
    _repo(code / "Projects/suite/backoffice")
    cfg = _projects(tmp_path, ["Projects/suite/webapp/", "Projects/suite/backoffice/"])

    assert derive.derive_roots(cfg, code) == [
        str(code / "Projects/suite/backoffice"),
        str(code / "Projects/suite/webapp"),
    ]


def test_nested_repository_is_dropped_when_a_parent_covers_it(tmp_path):
    """The hooks match on path prefix, so a child root would be dead weight."""
    code = tmp_path / "code"
    _repo(code / "Projects/acme")
    _repo(code / "Projects/acme/vendored")
    cfg = _projects(tmp_path, ["Projects/acme/", "Projects/acme/vendored/"])

    assert derive.derive_roots(cfg, code) == [str(code / "Projects/acme")]


def test_many_prefixes_in_one_repo_collapse_to_one_root(tmp_path):
    code = tmp_path / "code"
    _repo(code / "Projects/acme", "apps/a", "apps/b", "libs/c")
    cfg = _projects(tmp_path, [
        "Projects/acme/apps/a/", "Projects/acme/apps/b/", "Projects/acme/libs/c/",
    ])

    assert derive.derive_roots(cfg, code) == [str(code / "Projects/acme")]


def test_prefix_outside_any_repository_is_skipped(tmp_path):
    """No .git anywhere above it means no hook can ever fire for it."""
    code = tmp_path / "code"
    (code / "Projects/loose/src").mkdir(parents=True)
    cfg = _projects(tmp_path, ["Projects/loose/src/"])

    assert derive.derive_roots(cfg, code) == []


def test_missing_directory_is_skipped(tmp_path):
    """projects.yaml outliving a deleted project must not break the run."""
    code = tmp_path / "code"
    _repo(code / "Projects/acme")
    cfg = _projects(tmp_path, ["Projects/acme/", "Projects/gone/"])

    assert derive.derive_roots(cfg, code) == [str(code / "Projects/acme")]


def test_walk_up_stops_at_the_codebase_root(tmp_path):
    """A .git above the mount root must not drag an outside repo in."""
    code = tmp_path / "code"
    _repo(tmp_path)                      # .git at the parent of the mount
    (code / "Projects/acme").mkdir(parents=True)
    cfg = _projects(tmp_path, ["Projects/acme/"])

    assert derive.derive_roots(cfg, code) == []


def test_empty_config_derives_nothing(tmp_path):
    code = tmp_path / "code"
    code.mkdir()
    cfg = _projects(tmp_path, [])
    assert derive.derive_roots(cfg, code) == []


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def test_written_files_are_marked_generated(tmp_path, monkeypatch):
    """The installer refuses to clobber a hand-written list, and tells the two
    apart by this marker."""
    code = tmp_path / "code"
    _repo(code / "Projects/acme")
    cfg = _projects(tmp_path, ["Projects/acme/"])
    out = tmp_path / "cfgdir"

    monkeypatch.setattr(sys, "argv", [
        "derive-hook-repos.py",
        "--projects-config", str(cfg),
        "--codebase-root", str(code),
        "--config-dir", str(out),
    ])
    assert derive.main() == 0

    for name in ("repos", "index-repos"):
        text = (out / name).read_text()
        assert "# Generated by scripts/derive-hook-repos.py" in text
        assert str(code / "Projects/acme") in text


def test_refuses_to_write_empty_lists(tmp_path, monkeypatch):
    """Writing empty lists would silently switch every hook off."""
    code = tmp_path / "code"
    code.mkdir()
    cfg = _projects(tmp_path, [])
    out = tmp_path / "cfgdir"

    monkeypatch.setattr(sys, "argv", [
        "derive-hook-repos.py",
        "--projects-config", str(cfg),
        "--codebase-root", str(code),
        "--config-dir", str(out),
    ])
    assert derive.main() == 1
    assert not out.exists()
