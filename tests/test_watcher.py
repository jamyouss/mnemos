from watcher.main import PathRouter, should_ignore


def test_path_router_moby():
    router = PathRouter(codebase_root="/data/codebase", config_root="/data/claude-config")
    collection, rel_path = router.route("/data/codebase/moby/services/core/main.go")
    assert collection == "mnemos_code_moby"
    assert rel_path == "moby/services/core/main.go"


def test_path_router_trevio():
    router = PathRouter(codebase_root="/data/codebase", config_root="/data/claude-config")
    collection, rel_path = router.route("/data/codebase/trevio/go-modules/ddd/entity.go")
    assert collection == "mnemos_code_trevio"
    assert rel_path == "trevio/go-modules/ddd/entity.go"


def test_path_router_skills():
    router = PathRouter(codebase_root="/data/codebase", config_root="/data/claude-config")
    collection, rel_path = router.route("/data/claude-config/skills/moby-expert/instructions.md")
    assert collection == "mnemos_skills"
    assert rel_path == "skills/moby-expert/instructions.md"


def test_path_router_unknown():
    router = PathRouter(codebase_root="/data/codebase", config_root="/data/claude-config")
    result = router.route("/some/random/path.txt")
    assert result is None


def test_should_ignore():
    assert should_ignore("/data/codebase/moby/node_modules/pkg/index.js") is True
    assert should_ignore("/data/codebase/moby/.git/HEAD") is True
    assert should_ignore("/data/codebase/moby/services/core/main.go") is False
    assert should_ignore("/data/codebase/moby/dist/bundle.js") is True
    assert should_ignore("/data/codebase/moby/app.min.js") is True
