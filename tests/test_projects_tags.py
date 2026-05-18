from __future__ import annotations

from core.projects import detect_tags


# ---------------------------------------------------------------------------
# detect_tags — default (no overrides)
# ---------------------------------------------------------------------------


def test_detect_tags_default_emits_hierarchical_segments():
    # Zero-config users get cumulative path prefixes as tags, so they can
    # filter by any level of the directory hierarchy out of the box.
    assert detect_tags("foo/bar/baz/file.go") == ["foo", "foo/bar", "foo/bar/baz"]


def test_detect_tags_default_single_segment():
    assert detect_tags("topdir/file.go") == ["topdir"]


def test_detect_tags_default_empty_or_absolute_returns_empty():
    assert detect_tags("") == []
    assert detect_tags("/abs/path") == []


def test_detect_tags_default_skips_dot_segments():
    # './file.go' normalises to 'file.go' — single bare filename, no parent.
    assert detect_tags("./file.go") == ["file.go"]
    assert detect_tags("../file.go") == []


# ---------------------------------------------------------------------------
# detect_tags — explicit overrides
# ---------------------------------------------------------------------------


def test_detect_tags_override_emits_explicit_list():
    overrides = {
        "myproject/services/": ["my-service", "myproject", "go"],
    }
    assert detect_tags(
        "myproject/services/handler.go", overrides
    ) == ["my-service", "myproject", "go"]


def test_detect_tags_longest_prefix_wins():
    overrides = {
        "wrapper/":     ["outer"],
        "wrapper/sub/": ["inner", "outer"],
    }
    assert detect_tags("wrapper/sub/file.go", overrides) == ["inner", "outer"]
    assert detect_tags("wrapper/other/file.go", overrides) == ["outer"]


def test_detect_tags_falls_back_to_default_when_no_override_matches():
    overrides = {"unrelated/": ["x"]}
    assert detect_tags("myproject/file.go", overrides) == ["myproject"]


def test_detect_tags_empty_overrides_uses_default():
    assert detect_tags("project-x/file.go", {}) == ["project-x"]


def test_detect_tags_normalises_missing_trailing_slash():
    overrides = {"myproject/services": ["alias"]}
    assert detect_tags("myproject/services/handler.go", overrides) == ["alias"]
    assert detect_tags("myproject/services_v2/handler.go", overrides) == ["myproject", "myproject/services_v2"]
