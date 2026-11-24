import sys

import pytest

from watcher.main import PathRouter, _install_inotify_prune, should_ignore


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="inotify exists on Linux only")
def test_the_inotify_prune_patch_applies_to_the_installed_watchdog():
    # watchdog 7 renamed the class this patches: the watcher then watched
    # node_modules too and crash-looped on the inotify watch limit.
    assert _install_inotify_prune() is True


def test_path_router_myproject():
    router = PathRouter(codebase_root="/data/codebase", config_root="/data/claude-config")
    collection, rel_path = router.route("/data/codebase/myproject/services/core/main.go")
    assert collection == "mnemos_code"
    assert rel_path == "myproject/services/core/main.go"


def test_path_router_otherproject():
    router = PathRouter(codebase_root="/data/codebase", config_root="/data/claude-config")
    collection, rel_path = router.route("/data/codebase/otherproject/go-modules/ddd/entity.go")
    assert collection == "mnemos_code"
    assert rel_path == "otherproject/go-modules/ddd/entity.go"


def test_path_router_skills():
    router = PathRouter(codebase_root="/data/codebase", config_root="/data/claude-config")
    collection, rel_path = router.route("/data/claude-config/skills/project-expert/instructions.md")
    assert collection == "mnemos_skills"
    assert rel_path == "skills/project-expert/instructions.md"


def test_path_router_unknown():
    router = PathRouter(codebase_root="/data/codebase", config_root="/data/claude-config")
    result = router.route("/some/random/path.txt")
    assert result is None


def test_should_ignore():
    assert should_ignore("/data/codebase/myproject/node_modules/pkg/index.js") is True
    assert should_ignore("/data/codebase/myproject/.git/HEAD") is True
    assert should_ignore("/data/codebase/myproject/services/core/main.go") is False
    assert should_ignore("/data/codebase/myproject/dist/bundle.js") is True
    assert should_ignore("/data/codebase/myproject/app.min.js") is True


def test_flush_sends_the_api_key(monkeypatch):
    from unittest.mock import patch

    from watcher.main import DebouncedHandler

    monkeypatch.setenv("MNEMOS_API_KEY", "secret-a")
    router = PathRouter(codebase_root="/data/codebase", config_root="/data/claude-config")
    handler = DebouncedHandler(router)
    handler._pending["/data/codebase/p/main.go"] = ("mnemos_code", "p/main.go", "modified")

    with patch("watcher.main.httpx.post") as mock_post:
        handler._flush()

    assert mock_post.call_args.kwargs["headers"] == {"Authorization": "Bearer secret-a", "X-Mnemos-Client": "watcher"}
