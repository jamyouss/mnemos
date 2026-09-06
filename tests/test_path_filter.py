"""Tests for the unified path filter (core.path_filter).

This module is the single source of truth used by:
- watcher/main.py (early-skip before POSTing /internal/reindex)
- server/api.py (bulk reindex, via Indexer.should_skip)
- packages/core/indexer.py (defensive chokepoint inside index_file)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.path_filter import should_skip_path


# ---------------------------------------------------------------------------
# Non-regression: things that must stay indexable
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/data/codebase/myproject/services/handler.go",
        "/data/codebase/myorg/api/handler.py",
        "/data/codebase/x/services/myapp/data/seed.go",  # 'data' as legit dir
        "/data/codebase/x/web/public/index.html",        # legit Vue public/
        "/data/claude-config/skills/foo/SKILL.md",
        "/data/codebase/x/services/auth/middleware.ts",
    ],
)
def test_legitimate_paths_kept(path: str) -> None:
    assert should_skip_path(path) is False
    assert should_skip_path(Path(path)) is False


# ---------------------------------------------------------------------------
# Existing patterns: must keep skipping these
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/data/codebase/x/node_modules/lib/index.js",
        "/data/codebase/x/y/node_modules/pkg/foo.ts",
        "/data/codebase/x/dist/bundle.js",
        "/data/codebase/x/build/main.go",
        "/data/codebase/x/.nuxt/server.mjs",
        "/data/codebase/x/_nuxt/abc.js",
        "/data/codebase/x/.terraform/modules/aks/README.md",
        "/data/codebase/x/backup/data/grafana/plugins/file.json",
        "/data/codebase/x/foo.lock",
        "/data/codebase/x/logo.png",
        "/data/codebase/x/binary.so",
        "/data/codebase/x/package-lock.json",
        "/data/codebase/x/CHANGELOG.md",
        "/data/codebase/x/pnpm-lock.yaml",
        "/data/codebase/x/app.min.js",
        "/data/codebase/x/webapp/android/app/src/foo.java",
        "/data/codebase/x/webapp/ios/App/foo.swift",
        # Web assets packaged into the mobile builds — a second copy of the
        # app's own dist output, matched via IGNORE_PATH_SUBSTRINGS.
        "/data/codebase/x/webapp/android/app/src/main/assets/public/_nuxt/a.js",
        "/data/codebase/x/webapp/ios/App/App/public/_nuxt/a.js",
    ],
)
def test_existing_patterns_still_skipped(path: str) -> None:
    assert should_skip_path(path) is True


# ---------------------------------------------------------------------------
# New patterns: the actual fix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        # Yarn Berry PnP cache (the case we observed in the wild)
        "/data/codebase/acme-corp/acme/front/applications/widget/.yarn/yarn-4.9.4.cjs",
        "/data/codebase/x/.yarn/cache/some-pkg.zip",
        "/data/codebase/x/.yarn/releases/yarn-3.2.0.cjs",
    ],
)
def test_yarn_pnp_skipped(path: str) -> None:
    assert should_skip_path(path) is True


@pytest.mark.parametrize(
    "path",
    [
        # Any .cjs is vendored bundle (per user policy)
        "/data/codebase/x/.pnp.cjs",
        "/data/codebase/x/yarn-4.9.4.cjs",
        "/data/codebase/x/some/dir/legacy-bundle.cjs",
    ],
)
def test_cjs_extension_skipped(path: str) -> None:
    assert should_skip_path(path) is True


@pytest.mark.parametrize(
    "path",
    [
        # Generated test reports (the other case we saw)
        "/data/codebase/x/others/webdriverio-phoenix/report-tnr-gherkin-analysis.html",
        "/data/codebase/x/some/report-tnr-features.html",
    ],
)
def test_generated_reports_skipped(path: str) -> None:
    assert should_skip_path(path) is True


@pytest.mark.parametrize(
    "path",
    [
        # Multi-extension bundle artefacts
        "/data/codebase/x/dist/app.bundle.js",
        "/data/codebase/x/dist/main.chunk.js",
        "/data/codebase/x/dist/main.min.js",
    ],
)
def test_bundle_artefacts_skipped(path: str) -> None:
    assert should_skip_path(path) is True


@pytest.mark.parametrize(
    "path",
    [
        "/data/codebase/x/.parcel-cache/foo.bin",
        "/data/codebase/x/.svelte-kit/runtime/app.js",
        "/data/codebase/x/.turbo/cache/file.txt",
    ],
)
def test_extra_build_caches_skipped(path: str) -> None:
    assert should_skip_path(path) is True


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_empty_path_skipped() -> None:
    assert should_skip_path("") is True


def test_legitimate_index_html_kept() -> None:
    """A regular index.html in a Vue/Next public/ dir is not a report — must stay
    indexable. Only the report-* patterns are filtered, not all HTML."""
    assert should_skip_path("/data/codebase/x/web/public/index.html") is False


def test_partial_dir_name_does_not_match() -> None:
    """`my_node_modules_helper` is NOT `node_modules` — must not match.

    This is the reason we use Path.parts membership rather than substring on
    the directory deny set."""
    assert should_skip_path("/data/codebase/x/my_node_modules_helper/foo.js") is False
    assert should_skip_path("/data/codebase/x/distillery/main.go") is False  # not 'dist'


def test_accepts_pathlib_and_str() -> None:
    """The function must accept both ``str`` and ``pathlib.Path``."""
    assert should_skip_path("/data/codebase/x/.yarn/foo.cjs") is True
    assert should_skip_path(Path("/data/codebase/x/.yarn/foo.cjs")) is True


# ---------------------------------------------------------------------------
# archived/ — retired projects parked on disk
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/data/codebase/Projects/archived/legacy-shop/src/main.ts",
        "/data/codebase/Projects/archived/old-portal/pom.xml",
        # Nested anywhere in the tree, not just directly under a project root
        "/data/codebase/Projects/acme/archived/legacy-api/handler.go",
    ],
)
def test_archived_dir_skipped(path: str) -> None:
    assert should_skip_path(path) is True


@pytest.mark.parametrize(
    "path",
    [
        # Non-regression: a directory whose name merely contains "archived"
        # must stay indexed — only the exact path segment is banned.
        "/data/codebase/Projects/acme/archived-docs/adr-001.md",
        "/data/codebase/Projects/acme/src/unarchived/restore.go",
        # Same for files named after the concept.
        "/data/codebase/Projects/acme/src/archived.go",
        "/data/codebase/Projects/acme/src/archive/keep.go",
    ],
)
def test_archived_lookalikes_not_skipped(path: str) -> None:
    assert should_skip_path(path) is False


# ---------------------------------------------------------------------------
# Installed Python dependencies, whatever the virtualenv is called
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        # The case observed in the wild: a virtualenv named `sam-env`, which
        # no IGNORE_DIRS entry covers.
        "/data/codebase/Projects/acme/app/sam-env/lib/python3.14/site-packages/pip/_vendor/x.py",
        "/data/codebase/x/.direnv/python-3.12/lib/python3.12/site-packages/requests/api.py",
        "/data/codebase/x/env311/lib/python3.11/site-packages/urllib3/util.py",
        # Debian-style system install layout
        "/data/codebase/x/usr/lib/python3/dist-packages/yaml/loader.py",
    ],
)
def test_installed_python_packages_skipped(path: str) -> None:
    assert should_skip_path(path) is True


@pytest.mark.parametrize(
    "path",
    [
        # Non-regression: the project's own source must survive, including
        # files and directories merely named after the concept.
        "/data/codebase/Projects/acme/app/src/main.py",
        "/data/codebase/Projects/acme/docs/site-packages.md",
        "/data/codebase/Projects/acme/scripts/build_site_packages.py",
    ],
)
def test_project_python_sources_not_skipped(path: str) -> None:
    assert should_skip_path(path) is False


# ---------------------------------------------------------------------------
# Git worktrees — duplicate checkouts of already-indexed code
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        # Canonical segment, e.g. worktrees created under .claude/
        "/data/codebase/Projects/acme/corelib/.claude/worktrees/feat-api/handler.go",
        "/data/codebase/Projects/acme/worktrees/hotfix/main.go",
        # Parked beside the repo as `<name>.worktrees/` — the path segment is
        # `webapp.worktrees`, which no exact dir-name match would catch.
        "/data/codebase/Projects/acme/webshop/webapp.worktrees/fix-sprint3/app/page.vue",
        "/data/codebase/Projects/acme/api.worktrees/spike/server.go",
        # Tooling traces
        "/data/codebase/Projects/acme/webshop/.playwright-mcp/trace-001.md",
    ],
)
def test_worktrees_and_tool_traces_skipped(path: str) -> None:
    assert should_skip_path(path) is True


@pytest.mark.parametrize(
    "path",
    [
        # Non-regression: source that merely talks about worktrees stays indexed.
        "/data/codebase/Projects/acme/src/worktrees.go",
        "/data/codebase/Projects/acme/docs/worktrees.md",
        "/data/codebase/Projects/acme/internal/worktree/manager.go",
        # A directory named after the concept but not a worktree root.
        "/data/codebase/Projects/acme/worktrees-doc/guide.md",
    ],
)
def test_worktree_lookalikes_not_skipped(path: str) -> None:
    assert should_skip_path(path) is False


# ---------------------------------------------------------------------------
# Vendor bundles whose marker sits in the stem, not the extension
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/data/codebase/Projects/acme/app/public/legacy/scripts/chunk-vendors.js",
        "/data/codebase/Projects/acme/app/dist/chunk-vendors.4f2a1b.js",
    ],
)
def test_vendor_chunk_bundles_skipped(path: str) -> None:
    assert should_skip_path(path) is True


@pytest.mark.parametrize(
    "path",
    [
        # Non-regression: the app's own source about vendors stays indexed.
        "/data/codebase/Projects/acme/app/src/stores/vendors.ts",
        "/data/codebase/Projects/acme/app/src/chunk.ts",
    ],
)
def test_vendor_lookalikes_not_skipped(path: str) -> None:
    assert should_skip_path(path) is False


# ---------------------------------------------------------------------------
# Container runtime volumes of a local dev stack
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/data/codebase/Projects/acme/webshop/infra/local/infrastructure/data/consul/raft/wal/0001.wal",
        "/data/codebase/Projects/acme/webshop/infra/local/infrastructure/data/mongo/diagnostic.data/metrics.interim",
        "/data/codebase/Projects/acme/corelib/infra/local/data/minio/.minio.sys/tmp/abc-123",
    ],
)
def test_runtime_volumes_skipped(path: str) -> None:
    assert should_skip_path(path) is True


@pytest.mark.parametrize(
    "path",
    [
        # Non-regression: "data" is far too generic to ban outright, so the
        # project's own data-handling source and fixtures must survive.
        "/data/codebase/Projects/acme/webshop/src/data/countries.ts",
        "/data/codebase/Projects/acme/webshop/docs/data-model.md",
        "/data/codebase/Projects/acme/webshop/infra/local/docker-compose.yml",
        "/data/codebase/Projects/acme/webshop/infra/local/data-seed/seed.sql",
    ],
)
def test_data_lookalikes_not_skipped(path: str) -> None:
    assert should_skip_path(path) is False
# Per-run exclude mechanism
# ---------------------------------------------------------------------------


def test_tabular_data_not_denied_by_default() -> None:
    """CSV/TSV stay indexable: mnemos is not only a code RAG, and a silent
    default-deny loses data invisibly. Projects that only ever dump analytics
    exports turn them off through config/projects.yaml instead."""
    assert should_skip_path("/data/codebase/x/exports/run-2024-06-02.csv") is False
    assert should_skip_path("/data/codebase/x/reports/audit.tsv") is False


def test_extra_exts_opt_in() -> None:
    """--exclude-ext extends the deny list for this run; leading dot optional."""
    p = "/data/codebase/x/schema/service.proto"
    assert should_skip_path(p) is False
    assert should_skip_path(p, extra_exts=[".proto"]) is True
    assert should_skip_path(p, extra_exts=["proto"]) is True  # dot optional


def test_extra_dirs_opt_in() -> None:
    """--exclude-dir skips a directory anywhere in the path via parts membership,
    without matching a merely-similar sibling name."""
    p = "/data/codebase/x/mocks/fake_client.go"
    assert should_skip_path(p) is False
    assert should_skip_path(p, extra_dirs=["mocks"]) is True
    # near-miss: 'mockingbird' must NOT be caught by extra_dirs=['mocks']
    assert should_skip_path("/data/codebase/x/mockingbird/main.go", extra_dirs=["mocks"]) is False


def test_extras_default_noop() -> None:
    """Empty extras must not change behaviour for legitimate source files."""
    assert should_skip_path("/data/codebase/x/services/cart.ts") is False
    assert should_skip_path("/data/codebase/x/services/cart.ts", extra_exts=[], extra_dirs=[]) is False


# ---------------------------------------------------------------------------
# Coverage profiles
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/data/codebase/Projects/acme/corelib/coverage.out",
        "/data/codebase/Projects/acme/api/cover.out",
    ],
)
def test_coverage_profiles_skipped(path: str) -> None:
    assert should_skip_path(path) is True


@pytest.mark.parametrize(
    "path",
    [
        # Non-regression: source and docs merely named after the concept stay.
        "/data/codebase/Projects/acme/src/coverage.go",
        "/data/codebase/Projects/acme/docs/coverage.md",
        "/data/codebase/Projects/acme/src/output/handler.ts",
    ],
)
def test_coverage_lookalikes_not_skipped(path: str) -> None:
    assert should_skip_path(path) is False
