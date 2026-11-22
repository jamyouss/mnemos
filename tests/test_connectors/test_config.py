from __future__ import annotations

import textwrap

import pytest

from connectors.config import (
    ConfigError,
    ConfluenceConfig,
    JiraConfig,
    WebConfig,
    load_config,
    parse_interval,
)

ENV = {"WIKI_TOKEN": "wiki-pat", "TRACKER_TOKEN": "jira-pat"}


def _load(tmp_path, body: str, env: dict | None = None):
    path = tmp_path / "connectors.yaml"
    path.write_text(textwrap.dedent(body))
    return load_config(path, environ=ENV if env is None else env)


def test_loads_every_source_type_with_resolved_tokens(tmp_path):
    config = _load(tmp_path, """
        interval: 6h
        sources:
          - type: confluence
            url: https://wiki.example.com
            token_env: WIKI_TOKEN
            spaces: [eng, OPS]
            tags: [acme]
          - type: jira
            name: tracker
            url: https://example.atlassian.net
            user: bot@example.com
            token_env: TRACKER_TOKEN
            deployment: cloud
            projects: [OPS]
            jql: resolution = Unresolved
          - type: web
            urls: [https://docs.example.com/handbook/]
            max_pages: 50
    """)

    assert config.interval == 6 * 3600
    wiki, tracker, docs = config.sources
    assert wiki == ConfluenceConfig(
        name="confluence", url="https://wiki.example.com", token="wiki-pat", user=None,
        deployment="auto", spaces=("eng", "OPS"), tags=("acme",),
    )
    assert tracker == JiraConfig(
        name="tracker", url="https://example.atlassian.net", token="jira-pat",
        user="bot@example.com", deployment="cloud", projects=("OPS",),
        jql="resolution = Unresolved", tags=(),
    )
    assert docs == WebConfig(name="web", urls=("https://docs.example.com/handbook/",), max_pages=50, tags=())


def test_interval_is_optional(tmp_path):
    config = _load(tmp_path, """
        sources:
          - type: web
            urls: [https://docs.example.com/]
    """)
    assert config.interval is None
    assert config.sources[0].max_pages == 200


@pytest.mark.parametrize(
    ("value", "seconds"),
    [("45s", 45), ("30m", 1800), ("6h", 21600), ("1d", 86400), (900, 900), ("900", 900)],
)
def test_parse_interval(value, seconds):
    assert parse_interval(value) == seconds


@pytest.mark.parametrize("value", ["0", "-5m", "soon", "6 hours", 0, True])
def test_parse_interval_rejects_nonsense(value):
    with pytest.raises(ValueError):
        parse_interval(value)


def test_missing_token_env_var_is_reported_with_the_source(tmp_path):
    with pytest.raises(ConfigError, match=r"sources\[0\] \(confluence\).*WIKI_TOKEN"):
        _load(tmp_path, """
            sources:
              - type: confluence
                url: https://wiki.example.com
                token_env: WIKI_TOKEN
                spaces: [ENG]
        """, env={})


def test_every_problem_is_reported_at_once(tmp_path):
    with pytest.raises(ConfigError) as exc:
        _load(tmp_path, """
            intervall: 6h
            sources:
              - type: jira
                url: https://jira.example.com
                token_env: TRACKER_TOKEN
              - type: gitlab
              - type: web
                urls: [ftp://files.example.com/]
                max_page: 10
        """)
    message = str(exc.value)
    assert "unknown key 'intervall'" in message
    assert "sources[0] (jira): missing 'projects'" in message
    assert "sources[1]: unknown type 'gitlab'" in message
    assert "sources[2] (web): unknown key 'max_page'" in message
    assert "Not an http(s) URL" in message


def test_names_must_be_unique(tmp_path):
    with pytest.raises(ConfigError, match="duplicate source name 'web'"):
        _load(tmp_path, """
            sources:
              - type: web
                urls: [https://a.example.com/]
              - type: web
                urls: [https://b.example.com/]
        """)


def test_missing_file_is_a_config_error(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "absent.yaml", environ={})


def test_a_file_without_sources_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="no source"):
        _load(tmp_path, "interval: 1h\n")


def test_invalid_deployment_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="deployment"):
        _load(tmp_path, """
            sources:
              - type: confluence
                url: https://wiki.example.com
                token_env: WIKI_TOKEN
                spaces: [ENG]
                deployment: server
        """)


def test_the_shipped_example_is_valid():
    from pathlib import Path

    example = Path(__file__).parents[2] / "config" / "connectors.example.yaml"
    config = load_config(example, environ={"CONFLUENCE_TOKEN": "x", "JIRA_TOKEN": "y"})
    assert {s.type for s in config.sources} == {"confluence", "jira", "web", "git"}


def test_without_an_environment_token_names_are_checked_but_not_resolved():
    from connectors.config import parse_config

    raw = {"sources": [
        {"type": "jira", "url": "https://jira.example.com", "token_env": "UNSET_IN_THIS_PROCESS", "projects": ["OPS"]},
    ]}
    (tracker,) = parse_config(raw, environ=None).sources
    assert tracker.token == ""

    raw["sources"][0].pop("token_env")
    with pytest.raises(ConfigError) as exc:
        parse_config(raw, environ=None)
    assert exc.value.problems == [
        "sources[0] (jira): missing 'token_env' (name of the variable holding the token)"
    ]


def test_git_source_defaults_and_optional_token(tmp_path):
    from connectors.config import GitConfig

    config = _load(tmp_path, """
        sources:
          - type: git
            url: https://github.com/acme/public.git
          - type: git
            name: private
            url: https://gitlab.example.com/group/app
            token_env: TRACKER_TOKEN
            branch: develop
            paths: [docs/, src]
            exclude: ["*.lock"]
            max_file_kb: 64
            collection: mnemos_knowledge
    """)
    public, private = config.sources
    assert public == GitConfig(
        name="git", url="https://github.com/acme/public.git", token="", user=None, branch=None,
        paths=(), exclude=(), max_file_kb=512, collection="mnemos_code", tags=(),
    )
    assert (private.token, private.branch, private.paths, private.collection) == (
        "jira-pat", "develop", ("docs/", "src"), "mnemos_knowledge",
    )


def test_git_source_problems_are_listed(tmp_path):
    with pytest.raises(ConfigError) as exc:
        _load(tmp_path, """
            sources:
              - type: git
                url: git@github.com:acme/app.git
                collection: mnemos_memory
                max_file_kb: 0
        """)
    problems = exc.value.problems
    assert any("Not an http(s) repository URL" in p for p in problems)
    assert "sources[0] (git): 'collection' must be one of mnemos_code, mnemos_knowledge" in problems
    assert "sources[0] (git): 'max_file_kb' must be a positive integer" in problems
