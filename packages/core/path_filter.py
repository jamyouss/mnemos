"""Unified path-based ignore policy for indexing.

Single source of truth used by every ingestion path:

* ``watcher/main.py``  — early skip before POSTing ``/internal/reindex``
* ``server/api.py``    — bulk reindex walker (``_should_skip``)
* ``core.indexer``     — defensive chokepoint inside ``Indexer.index_file``

Why a separate module? Three near-duplicate lists used to live in those
three places and they drifted (e.g. ``.yarn/`` was missing everywhere,
``.cjs`` was missing everywhere, multi-extension semantics differed
between ``str.endswith`` and ``Path.suffix``). Centralising them here
fixes the drift and gives the indexer a defence-in-depth check so a push
API caller cannot bypass the policy.
"""

from __future__ import annotations

from pathlib import Path

# ---------------------------------------------------------------------------
# Deny lists
# ---------------------------------------------------------------------------

# Directory names — matched against ``Path.parts`` membership (so
# ``my_node_modules_helper`` does NOT match ``node_modules``).
IGNORE_DIRS: frozenset[str] = frozenset({
    # Dependencies & package managers
    "node_modules", ".pnpm-store", ".yarn", "vendor", "Pods",
    # Version control
    ".git",
    # Build outputs (web)
    "dist", "build", ".nuxt", ".output", "_nuxt", ".next", ".turbo",
    ".svelte-kit", ".parcel-cache",
    # Infra / IaC state
    ".terraform", "terraform.tfstate.d",
    # Local backup / data snapshots
    "backup", "backups",
    # Retired projects parked out of the way — kept on disk, not worth
    # retrieving: they answer questions about code that is no longer live.
    "archived",
    # Caches & tooling
    "__pycache__", ".nx", ".cache", ".pytest_cache", ".storybook",
    # Git worktrees — a second checkout of code that is already indexed at its
    # main path. Near-identical duplicates are the worst case for retrieval:
    # they compete with the original instead of adding anything.
    "worktrees",
    # Agent / tooling traces
    ".playwright-mcp",
    # IDE & editors
    ".idea", ".vscode",
    # Virtualenvs
    ".venv", "venv",
    # Test artifacts
    "test-results", "coverage",
})

# Full-path substrings — used when a directory name alone is too generic
# (e.g. ``android``, ``public``, ``data``) to ban globally. Must NOT match
# the container mount root ``/data/codebase/``.
IGNORE_PATH_SUBSTRINGS: tuple[str, ...] = (
    "/webapp/android/",            # Capacitor android build artefacts
    "/webapp/ios/",                # Capacitor ios build artefacts
    "/app/src/main/assets/",       # Android packaged web assets
    "/App/App/public/",            # iOS packaged web assets
    # Installed Python dependencies. Matched on the marker directory rather
    # than the virtualenv name, because those are arbitrary: `venv/` and
    # `.venv/` are in IGNORE_DIRS but `sam-env/`, `.direnv/`, `env311/` are
    # not, and each one drags a whole stdlib mirror into the index.
    "/site-packages/",
    "/dist-packages/",
    # Worktrees parked next to their repo as `<name>.worktrees/` — the segment
    # is not literally "worktrees", so IGNORE_DIRS alone cannot catch it.
    ".worktrees/",
    # Container runtime volumes of a local dev stack. Databases rewrite these
    # constantly (WAL segments, diagnostic dumps), so a watcher pointed at
    # them never stops re-indexing. "data" alone is far too generic to ban,
    # hence the anchored prefixes.
    "/infrastructure/data/",
    "/infra/local/data/",
    ".minio.sys/",
    "/diagnostic.data/",
)

# File extensions — matched via ``name.endswith(ext)`` so multi-part
# extensions like ``.min.js`` work (``Path.suffix`` would only see
# ``.js`` and miss them).
IGNORE_EXTS: tuple[str, ...] = (
    # Generated / minified JS bundles
    ".min.js", ".bundle.js", ".chunk.js", ".map",
    # Vendored CommonJS bundles — Yarn PnP cache, ``.pnp.cjs``, ``yarn-X.Y.Z.cjs``.
    # Rare to find legitimate ``.cjs`` source; policy choice: deny by default.
    ".cjs",
    # Lock / log
    ".lock", ".log",
    # Compiled
    ".pyc", ".o", ".a",
    # Images & fonts
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg",
    ".woff", ".woff2", ".ttf", ".eot",
    # Archives & binaries
    ".pdf", ".zip", ".tar", ".gz", ".exe", ".bin", ".so", ".dylib",
)

# Exact basenames.
IGNORE_FILENAMES: frozenset[str] = frozenset({
    "CHANGELOG.md",
    "pnpm-lock.yaml",
    "package-lock.json",
    "yarn.lock",
    "poetry.lock",
    ".last-run.json",
})

# Basename substrings — generated test / audit reports. We do NOT ban all
# ``.html`` because a Vue/Next app's ``index.html`` is legitimate source.
IGNORE_BASENAME_SUBSTRINGS: tuple[str, ...] = (
    "report-tnr-",              # WebdriverIO/Gherkin TNR analysis report
    ".lighthouse-report.html",
    "lighthouse-report-",
    # Vue CLI / webpack vendor bundle. Not caught by the ``.chunk.js`` and
    # ``.bundle.js`` extensions: the marker is in the stem, not the suffix.
    "chunk-vendors",
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def should_skip_path(path: str | Path) -> bool:
    """Return True if ``path`` must not be indexed.

    Callers should short-circuit before any chunking / embedding work.
    """
    if not path:
        return True

    p = path if isinstance(path, Path) else Path(path)
    name = p.name

    if set(p.parts) & IGNORE_DIRS:
        return True

    s = str(p)
    if any(sub in s for sub in IGNORE_PATH_SUBSTRINGS):
        return True

    if name in IGNORE_FILENAMES:
        return True

    if any(name.endswith(ext) for ext in IGNORE_EXTS):
        return True

    if any(pat in name for pat in IGNORE_BASENAME_SUBSTRINGS):
        return True

    return False
