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

from collections.abc import Iterable
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
    # Trash folders keep copies of deleted files (skills land in `.trash/`
    # when removed), which then compete with the live ones.
    ".trash",
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
    # Go coverage profiles. Machine-generated line tables that match almost
    # any query lexically — `coverage.out` was the single most-returned file
    # across a sample of real searches.
    ".out",
    # Compiled
    ".pyc", ".o", ".a",
    # Images & fonts
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg",
    ".woff", ".woff2", ".ttf", ".eot",
    # Archives & binaries
    ".pdf", ".zip", ".tar", ".gz", ".tgz", ".exe", ".bin", ".so", ".dylib",
    # Generated or copied, never authored: Jest snapshots, editor/tool
    # backups (`SKILL.md.bak` duplicated every skill it sat next to), and XML
    # Schemas, which only show up vendored (OOXML schemas shipped in skills).
    ".snap", ".bak", ".xsd",
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
    # Lighthouse CLI output: `<page>-<timestamp>.report.html|json`, 0.5-0.7 MB
    # each. The `lighthouse-report-` marker is often on the directory instead.
    ".report.html",
    ".report.json",
    # Vue CLI / webpack vendor bundle. Not caught by the ``.chunk.js`` and
    # ``.bundle.js`` extensions: the marker is in the stem, not the suffix.
    "chunk-vendors",
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def should_skip_path(
    path: str | Path,
    *,
    extra_exts: Iterable[str] = (),
    extra_dirs: Iterable[str] = (),
) -> bool:
    """Return True if ``path`` must not be indexed.

    Callers should short-circuit before any chunking / embedding work.

    ``extra_exts`` / ``extra_dirs`` extend the built-in deny lists for this
    call only — e.g. ``mnemos reindex --exclude-ext .proto --exclude-dir mocks``
    threads per-run excludes down to the walker. Both default to empty, so
    existing call sites (watcher, indexer, push API) are unaffected. Leading
    dots on ``extra_exts`` are optional (``"csv"`` == ``".csv"``); ``extra_dirs``
    match against ``Path.parts`` membership like the built-in ``IGNORE_DIRS``.
    """
    return skip_reason(path, extra_exts=extra_exts, extra_dirs=extra_dirs) is not None


def skip_reason(
    path: str | Path,
    *,
    extra_exts: Iterable[str] = (),
    extra_dirs: Iterable[str] = (),
) -> str | None:
    """Why ``path`` must not be indexed, in a few words; None when it may be.

    The decision of :func:`should_skip_path`, which is this call's truthiness:
    the dashboard's path tester shows the reason, the ingest paths only test it.
    """
    if not path:
        return "empty path"

    p = path if isinstance(path, Path) else Path(path)
    name = p.name
    parts = set(p.parts)

    if hit := parts & IGNORE_DIRS:
        return f"inside an ignored directory ({', '.join(sorted(hit))})"

    if extra_dirs and (hit := parts & {d.strip("/") for d in extra_dirs}):
        return f"inside an ignored directory ({', '.join(sorted(hit))})"

    s = str(p)
    if sub := next((sub for sub in IGNORE_PATH_SUBSTRINGS if sub in s), None):
        return f"path contains {sub!r}"

    if name in IGNORE_FILENAMES:
        return f"ignored file name {name!r}"

    if ext := next((ext for ext in IGNORE_EXTS if name.endswith(ext)), None):
        return f"ignored extension {ext!r}"

    if extra_exts and (ext := next(
        (e for e in extra_exts if name.endswith(e if e.startswith(".") else "." + e)), None,
    )):
        return f"ignored extension {ext!r}"

    if pat := next((pat for pat in IGNORE_BASENAME_SUBSTRINGS if pat in name), None):
        return f"file name contains {pat!r}"

    return None
