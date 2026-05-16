from __future__ import annotations

from pathlib import Path

from server.api import _should_skip


def test_legitimate_code_path_kept():
    """The container mount root is /data/codebase/ — nothing there must be skipped by accident."""
    assert _should_skip(Path("/data/codebase/myproject/services/handler.go")) is False
    assert _should_skip(Path("/data/codebase/myorg/api/handler.py")) is False
    assert _should_skip(Path("/data/claude-config/skills/foo/SKILL.md")) is False


def test_node_modules_skipped():
    assert _should_skip(Path("/data/codebase/x/node_modules/lib/index.js"))
    assert _should_skip(Path("/data/codebase/x/y/node_modules/pkg/foo.ts"))


def test_dist_and_build_skipped():
    assert _should_skip(Path("/data/codebase/x/dist/bundle.js"))
    assert _should_skip(Path("/data/codebase/x/build/main.go"))
    assert _should_skip(Path("/data/codebase/x/.nuxt/server.mjs"))
    assert _should_skip(Path("/data/codebase/x/_nuxt/abc.js"))


def test_mobile_build_substrings_skipped():
    assert _should_skip(Path("/data/codebase/x/webapp/android/app/src/foo.java"))
    assert _should_skip(Path("/data/codebase/x/webapp/ios/App/foo.swift"))
    assert _should_skip(Path("/data/codebase/x/webapp/android/app/src/main/assets/public/_nuxt/a.js"))
    assert _should_skip(Path("/data/codebase/x/webapp/ios/App/App/public/_nuxt/a.js"))


def test_terraform_state_skipped():
    assert _should_skip(Path("/data/codebase/x/.terraform/modules/aks/README.md"))


def test_backup_skipped():
    assert _should_skip(Path("/data/codebase/x/backup/data/grafana/plugins/file.json"))


def test_binary_extensions_skipped():
    """Path.suffix returns only the last extension, so e.g. `.min.js` in the
    set is never matched by `bundle.min.js` (suffix='.js'). That's a known
    limitation — single-extension entries do work."""
    assert _should_skip(Path("/data/codebase/x/foo.lock"))
    assert _should_skip(Path("/data/codebase/x/logo.png"))
    assert _should_skip(Path("/data/codebase/x/binary.so"))


def test_lockfiles_skipped():
    assert _should_skip(Path("/data/codebase/x/package-lock.json"))
    assert _should_skip(Path("/data/codebase/x/CHANGELOG.md"))
    assert _should_skip(Path("/data/codebase/x/pnpm-lock.yaml"))


def test_genuine_data_dirs_not_skipped():
    """A user dir called 'data' inside a real project must NOT trigger the skip
    — only build-output substrings should."""
    assert _should_skip(Path("/data/codebase/x/services/myapp/data/seed.go")) is False


def test_public_assets_dir_not_skipped_outside_mobile_path():
    """A 'public' folder in a Vue/Next project is normal source content; only
    the mobile-packaged variant should be skipped via path substring."""
    assert _should_skip(Path("/data/codebase/x/web/public/index.html")) is False
