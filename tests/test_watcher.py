from watcher.main import PathRouter, should_ignore


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
