from __future__ import annotations

import base64
import os
import subprocess

import pytest

from connectors.git import GitSource, browse_url, file_path, git_env, repo_scope
from connectors.sync import SkipItem, SourceError, sync_scope

from .conftest import FakeMnemos

SCOPE = "example.com/acme/demo"


def _git(cwd, *args: str, when: int | None = None) -> None:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
    }
    if when is not None:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = f"@{when} +0000"
    subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True)


def _commit(repo, files: dict[str, bytes | None], when: int) -> None:
    for name, data in files.items():
        path = repo / name
        if data is None:
            _git(repo, "rm", "-q", name)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        _git(repo, "add", name)
    _git(repo, "commit", "-q", "-m", f"at {when}", when=when)


@pytest.fixture
def origin(tmp_path, monkeypatch):
    # A user's global git config (insteadOf, hooks…) must not leak into the clones.
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    repo = tmp_path / "origin"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "uploadpack.allowFilter", "true")
    _commit(repo, {
        "README.md": b"# Demo\n",
        "src/app.go": b"package main\n",
        "src/app_test.go": b"package main\n",
        "docs/guide.md": b"# Guide\n",
        "assets/logo.png": b"\x89PNG not even read",
        "data/blob.dat": b"head\0tail",
        "data/big.txt": b"x" * 3000,
    }, when=1_000)
    return repo


def _source(origin, **kwargs) -> GitSource:
    return GitSource(f"file://{origin}", **kwargs)


def test_lists_files_dated_by_their_last_commit(origin):
    _commit(origin, {"src/app.go": b"package main\n\nfunc main() {}\n"}, when=2_000)

    items = {i.title: i for i in _source(origin).items(SCOPE)}

    # Binary by extension: never listed, so never downloaded.
    assert sorted(items) == [
        "README.md", "data/big.txt", "data/blob.dat", "docs/guide.md", "src/app.go", "src/app_test.go",
    ]
    assert items["src/app.go"].updated == 2_000
    assert items["README.md"].updated == 1_000
    assert items["src/app.go"].file_path == "git/example.com/acme~demo/src/app.go"


def test_paths_and_exclude_narrow_the_listing(origin):
    items = _source(origin, paths=["src/", "/docs"], exclude=["*_test.go"]).items(SCOPE)
    assert sorted(i.title for i in items) == ["docs/guide.md", "src/app.go"]


def test_contents_are_read_lazily_and_oversized_or_binary_files_are_skipped(origin):
    # Read while iterating, as the sync engine does: the clone lives as long as the listing.
    for item in _source(origin, max_file_kb=1).items(SCOPE):
        if item.title == "src/app.go":
            assert item.markdown() == "package main\n"
        elif item.title == "data/big.txt":
            with pytest.raises(SkipItem, match="over the 1 KB limit"):
                item.markdown()
        elif item.title == "data/blob.dat":
            with pytest.raises(SkipItem, match="binary"):
                item.markdown()


def test_a_second_sync_pushes_only_what_changed_and_drops_what_is_gone(origin):
    mnemos = FakeMnemos(collection="mnemos_code")
    client = mnemos.client().for_collection("mnemos_code")

    def sync():
        source = _source(origin, paths=["src", "docs", "README.md"])
        return sync_scope(client, source="git", scope=SCOPE, items=source.items(SCOPE))

    first = sync()
    assert (first.pushed, first.unchanged, first.deleted) == (4, 0, 0)
    assert mnemos.pushed[0]["tags"] == ["git", f"git:{SCOPE}"]

    _commit(origin, {"src/app.go": b"package main // v2\n", "docs/guide.md": None}, when=2_000)
    mnemos.pushed.clear()
    second = sync()

    assert (second.pushed, second.unchanged, second.deleted) == (1, 2, 1)
    assert mnemos.pushed_paths == ["git/example.com/acme~demo/src/app.go"]
    assert mnemos.deleted == ["git/example.com/acme~demo/docs/guide.md"]


def test_a_failed_clone_is_a_source_error(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    with pytest.raises(SourceError, match="git clone failed"):
        list(GitSource(f"file://{tmp_path / 'absent'}").items(SCOPE))


@pytest.mark.parametrize(("url", "scope"), [
    ("https://GitHub.com/acme/demo.git", "github.com/acme/demo"),
    ("https://user@gitlab.example.com:443/group/sub/repo/", "gitlab.example.com/group/sub/repo"),
    ("http://git.local:8080/a/b", "git.local:8080/a/b"),
])
def test_repo_scope_normalizes_every_spelling(url, scope):
    assert repo_scope(url) == scope


@pytest.mark.parametrize("url", ["git@github.com:acme/demo.git", "https://github.com/", "ftp://x/y"])
def test_repo_scope_rejects_non_http_urls(url):
    with pytest.raises(ValueError):
        repo_scope(url)


def test_owner_and_repo_share_one_path_segment():
    # `build` as a directory would be dropped by the server's path filter.
    assert file_path("gitlab.com/build/sub/vendor", "a/b.go") == "git/gitlab.com/build~sub~vendor/a/b.go"


def test_browse_url_per_forge():
    assert browse_url("https://github.com/acme/demo.git", "main", "a.go") == "https://github.com/acme/demo/blob/main/a.go"
    assert browse_url("https://gitlab.example.com/g/r", "dev", "a.go") == "https://gitlab.example.com/g/r/-/blob/dev/a.go"
    assert browse_url("https://git.example.com/g/r.git", "main", "a.go") == "https://git.example.com/g/r"


def test_the_token_is_a_header_scoped_to_the_repository_origin():
    env = git_env("https://user@gitlab.example.com/g/r.git", None, "s3cret")
    config = {env[f"GIT_CONFIG_KEY_{i}"]: env[f"GIT_CONFIG_VALUE_{i}"] for i in range(int(env["GIT_CONFIG_COUNT"]))}
    expected = base64.b64encode(b"oauth2:s3cret").decode()
    assert config["http.https://gitlab.example.com/.extraHeader"] == f"Authorization: Basic {expected}"
    assert git_env("https://github.com/a/b", None, None)["GIT_CONFIG_COUNT"] == "1"  # quotePath only
