"""Tests for the unified path filter (core.path_filter).

This module is the single source of truth used by:
- watcher/main.py (early-skip before POSTing /internal/reindex)
- server/api.py (bulk reindex via _should_skip)
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
        "/data/codebase/acme-corp/acme/front/applications/colibri/.yarn/yarn-4.9.4.cjs",
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
