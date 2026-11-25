"""Git connector: the files of a remote repository, on any HTTPS host
(GitHub, GitLab, Gitea, self-hosted).

Each run makes a *blobless* clone (``--filter=blob:none``): every commit and
tree, no file contents. That is enough to list the files and date each one
by the last commit touching it, in one ``git log``; contents are fetched
only for the files the sync actually pushes, a batch per request
(:meth:`GitSource.prefetch`). Unchanged files cost no download and no
embedding. Nothing outlives the run: the clone sits in a temporary
directory removed by :meth:`GitSource.close`, and Mnemos remains the only state.

A token, when given, travels as an HTTP header scoped to the repository's
origin through ``GIT_CONFIG_*`` variables: it never appears in a URL, a
command line or ``.git/config``.
"""

from __future__ import annotations

import base64
import fnmatch
import logging
import os
import shutil
import subprocess
import tempfile
import weakref
from collections.abc import Callable, Iterable, Iterator
from functools import partial
from urllib.parse import urlsplit

from connectors.sync import SkipItem, SourceError, SourceItem

logger = logging.getLogger(__name__)

SOURCE = "git"
COLLECTIONS = ("mnemos_code", "mnemos_knowledge")
DEFAULT_COLLECTION = "mnemos_code"
DEFAULT_MAX_FILE_KB = 512
DEFAULT_USER = "oauth2"  # GitLab expects it with a token; GitHub ignores the user name

_DEFAULT_PORTS = {"http": 80, "https": 443}
_CLONE_TIMEOUT = 1800.0
_COMMAND_TIMEOUT = 300.0
# Never downloaded: binary whatever their content. Saves fetching a blob
# only to throw it away; the server's path filter drops more on its side.
_BINARY_EXTENSIONS = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp", ".tif", ".tiff", ".psd",
    ".pdf", ".zip", ".gz", ".tgz", ".tar", ".bz2", ".xz", ".7z", ".rar", ".jar", ".war",
    ".exe", ".dll", ".so", ".dylib", ".a", ".o", ".bin", ".class", ".pyc", ".wasm",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".mp3", ".mp4", ".mov", ".avi", ".webm", ".wav", ".ogg",
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".odt",
    ".sqlite", ".db", ".parquet",
})
_BINARY_SNIFF = 8000  # bytes checked for a NUL, like git's own heuristic


def repo_scope(url: str) -> str:
    """``host[:port]/owner/repo`` of an http(s) repository URL — the sync scope.

    Lower-cases the host, drops credentials, the default port, a trailing
    ``/`` and ``.git``, so every spelling of one repository maps to one scope.
    """
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError as exc:
        raise ValueError(f"Not an http(s) repository URL: {url!r}") from exc
    scheme = parts.scheme.lower()
    path = parts.path.strip("/").removesuffix(".git").strip("/")
    if scheme not in _DEFAULT_PORTS or not parts.hostname or not path:
        raise ValueError(f"Not an http(s) repository URL: {url!r}")
    host = parts.hostname.lower()
    netloc = host if port in (None, _DEFAULT_PORTS[scheme]) else f"{host}:{port}"
    return f"{netloc}/{path}"


def file_path(scope: str, path: str) -> str:
    """``git/<host>/<owner~repo>/<path in repo>``.

    The repository path is one segment joined with ``~`` (not allowed in
    GitHub or GitLab names, so unambiguous): an owner or repository called
    ``build`` or ``vendor`` must not land in a directory the server's path
    filter drops. Directories *inside* the repository are filtered as usual.
    """
    host, _, repo = scope.partition("/")
    return f"git/{host}/{repo.replace('/', '~')}/{path}"


def browse_url(url: str, branch: str, path: str) -> str:
    """Link to the file on the forge's web UI; the repository page when the
    forge is not recognised."""
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    base = f"{parts.scheme}://{parts.netloc.rpartition('@')[2]}/{parts.path.strip('/').removesuffix('.git')}"
    if host == "github.com":
        return f"{base}/blob/{branch}/{path}"
    if "gitlab" in host:
        return f"{base}/-/blob/{branch}/{path}"
    return base


def git_env(url: str, user: str | None, token: str | None) -> dict[str, str]:
    """Git config for every command, as environment: unquoted paths, and the
    token sent to the repository's origin only."""
    config = {"core.quotePath": "false"}
    if token:
        parts = urlsplit(url.strip())
        origin = f"{parts.scheme}://{parts.netloc.rpartition('@')[2]}/"
        credentials = base64.b64encode(f"{user or DEFAULT_USER}:{token}".encode()).decode()
        config[f"http.{origin}.extraHeader"] = f"Authorization: Basic {credentials}"
    env = {"GIT_CONFIG_COUNT": str(len(config))}
    for i, (key, value) in enumerate(config.items()):
        env[f"GIT_CONFIG_KEY_{i}"] = key
        env[f"GIT_CONFIG_VALUE_{i}"] = value
    return env


class GitSource:
    """List the files of one branch of a repository, with lazy contents."""

    source = SOURCE

    def __init__(
        self,
        url: str,
        *,
        user: str | None = None,
        token: str | None = None,
        branch: str | None = None,
        paths: Iterable[str] = (),
        exclude: Iterable[str] = (),
        max_file_kb: int = DEFAULT_MAX_FILE_KB,
        run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    ) -> None:
        self._url = url.strip()
        self._branch = branch
        self._paths = [p.strip().strip("/") for p in paths if p.strip().strip("/")]
        self._exclude = list(exclude)
        self._max_bytes = max_file_kb * 1024
        self._run = run
        self._env = {
            **os.environ,
            # Fail instead of waiting for a password nobody will type.
            "GIT_TERMINAL_PROMPT": "0",
            **git_env(self._url, user, token),
        }
        # Same private-CA setting as the HTTP connectors; git does not read it itself.
        if os.environ.get("SSL_CERT_FILE") and "GIT_SSL_CAINFO" not in os.environ:
            self._env["GIT_SSL_CAINFO"] = os.environ["SSL_CERT_FILE"]
        self._repo: str | None = None
        self._cleanup: weakref.finalize | None = None
        self._shas: dict[str, str] = {}  # file_path → blob sha, for prefetch

    def __enter__(self) -> GitSource:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        """Remove the clone. Contents can no longer be read afterwards."""
        if self._cleanup is not None:
            self._cleanup()
        self._cleanup, self._repo = None, None
        self._shas.clear()

    def items(self, scope: str) -> Iterator[SourceItem]:
        """The files of the branch. The clone outlives the listing, until :meth:`close`,
        so contents stay readable after the last item is yielded."""
        self.close()
        repo = self._repo = tempfile.mkdtemp(prefix="mnemos-git-")
        # Removed on close(), or when the source is garbage-collected without it.
        self._cleanup = weakref.finalize(self, shutil.rmtree, repo, True)
        clone = ["clone", "--quiet", "--filter=blob:none", "--no-checkout", "--single-branch"]
        if self._branch:
            clone += ["--branch", self._branch]
        self._git(*clone, "--", self._url, repo, timeout=_CLONE_TIMEOUT)
        branch = self._branch or self._git("symbolic-ref", "--short", "HEAD", cwd=repo).strip()
        updated = self._last_changes(repo)
        for sha, path in self._files(repo):
            item_path = file_path(scope, path)
            self._shas[item_path] = sha
            yield SourceItem(
                file_path=item_path,
                title=path,
                source_url=browse_url(self._url, branch, path),
                updated=updated.get(path),
                content=partial(self._read, repo, sha, path),
            )

    def prefetch(self, items: Iterable[SourceItem]) -> None:
        """Download the contents of ``items`` in one request.

        Without it, the blobless clone fetches each blob on first read: one
        round trip per file, which dominates a first sync over a slow link.
        """
        shas = [self._shas[i.file_path] for i in items if i.file_path in self._shas]
        if not shas or self._repo is None:
            return
        self._git_bytes(
            "-c", "fetch.negotiationAlgorithm=noop", "fetch", "origin", "--no-tags", "--no-write-fetch-head",
            "--recurse-submodules=no", "--filter=blob:none", "--stdin",
            cwd=self._repo, input="\n".join(shas).encode() + b"\n",
        )

    def _wanted(self, path: str) -> bool:
        if os.path.splitext(path)[1].lower() in _BINARY_EXTENSIONS:
            return False
        return not any(fnmatch.fnmatchcase(path, pattern) for pattern in self._exclude)

    def _files(self, repo: str) -> Iterator[tuple[str, str]]:
        """``(blob sha, path)`` of every regular file of HEAD within ``paths``."""
        out = self._git("ls-tree", "-r", "-z", "--full-tree", "HEAD", "--", *self._paths, cwd=repo)
        for entry in out.split("\0"):
            if not entry:
                continue
            meta, _, path = entry.partition("\t")
            mode, kind, sha = meta.split(" ")
            # Symlinks (120000) and submodules (commit) are not file contents.
            if kind == "blob" and mode in ("100644", "100755") and self._wanted(path):
                yield sha, path

    def _last_changes(self, repo: str) -> dict[str, float]:
        """Path → commit time of the last commit touching it.

        Newest first, so the first time a path shows up is its last change.
        ``--no-renames``: rename detection would download blobs.
        """
        out = self._git(
            "log", "--no-renames", "--name-only", "--format=%x00%ct", "HEAD", "--", *self._paths,
            cwd=repo,
        )
        updated: dict[str, float] = {}
        when: float | None = None
        for line in out.split("\n"):
            if line.startswith("\0"):
                when = float(line[1:])
            elif line and when is not None:
                updated.setdefault(line, when)
        return updated

    def _read(self, repo: str, sha: str, path: str) -> str:
        data = self._git_bytes("cat-file", "blob", sha, cwd=repo)
        if len(data) > self._max_bytes:
            raise SkipItem(f"{path}: {len(data) // 1024} KB, over the {self._max_bytes // 1024} KB limit")
        if b"\0" in data[:_BINARY_SNIFF]:
            raise SkipItem(f"{path}: binary")
        return data.decode("utf-8", errors="replace")

    def _git(self, *args: str, cwd: str | None = None, timeout: float = _COMMAND_TIMEOUT) -> str:
        return self._git_bytes(*args, cwd=cwd, timeout=timeout).decode("utf-8", errors="replace")

    def _git_bytes(
        self, *args: str, cwd: str | None = None, timeout: float = _COMMAND_TIMEOUT, input: bytes | None = None,
    ) -> bytes:
        try:
            done = self._run(
                ["git", *args], cwd=cwd, env=self._env, input=input, capture_output=True, timeout=timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            # The git binary, or the clone directory once the source is closed.
            raise SourceError(f"cannot run git: {exc}") from exc
        except subprocess.TimeoutExpired as exc:
            raise SourceError(f"git {args[0]} timed out after {timeout:.0f}s") from exc
        if done.returncode != 0:
            # The last lines carry the reason; the token is in a header, never echoed.
            reason = done.stderr.decode("utf-8", errors="replace").strip().splitlines()[-3:]
            raise SourceError(f"git {args[0]} failed: {' '.join(reason) or f'exit {done.returncode}'}")
        return done.stdout
