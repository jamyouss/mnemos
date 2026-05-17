from __future__ import annotations

from pathlib import Path

import pytest

from core.projects import detect_project, load_project_overrides


# ---------------------------------------------------------------------------
# detect_project
# ---------------------------------------------------------------------------


def test_detect_first_segment_by_default():
    assert detect_project("myproject/services/handler.go") == "myproject"
    assert detect_project("otherproject/cmd/main.go") == "otherproject"


def test_detect_returns_none_on_empty_or_absolute():
    assert detect_project("") is None
    assert detect_project("/absolute/path/file.go") is None


def test_detect_strips_dot_segments():
    # Path normalises './file.go' to 'file.go' (no '.' part survives).
    # Reasonable behaviour: treat the bare filename as the "project name"
    # only when there's nothing more specific; here we expect 'file.go'
    # because the user gave us no parent directory. This is the
    # zero-information case; the consumer should not have asked.
    assert detect_project("./file.go") == "file.go"
    # '..' is a parent-traversal segment and must NOT become a project name.
    assert detect_project("../file.go") is None


def test_detect_yaml_override_wins_over_default():
    overrides = {"shared-lib": ["myproject/libs/shared/"]}
    # Default would return "myproject"; override returns "shared-lib".
    assert detect_project(
        "myproject/libs/shared/utils/foo.go", overrides
    ) == "shared-lib"


def test_detect_longest_prefix_wins():
    overrides = {
        "outer": ["wrapper/"],
        "inner": ["wrapper/sub/"],
    }
    assert detect_project("wrapper/sub/file.go", overrides) == "inner"
    assert detect_project("wrapper/other/file.go", overrides) == "outer"


def test_detect_falls_back_when_no_override_matches():
    overrides = {"weirdname": ["very/specific/prefix/"]}
    assert detect_project("myproject/file.go", overrides) == "myproject"


def test_detect_prefix_normalisation():
    """A prefix written without a trailing slash should still match path-aligned."""
    overrides = {"alias": ["myproject/services"]}
    # 'myproject/services/...' matches; 'myproject/services_v2/...' must NOT match
    assert detect_project("myproject/services/handler.go", overrides) == "alias"
    assert detect_project("myproject/services_v2/handler.go", overrides) == "myproject"


def test_detect_empty_overrides_dict_uses_default():
    assert detect_project("project-x/file.go", {}) == "project-x"


# ---------------------------------------------------------------------------
# load_project_overrides
# ---------------------------------------------------------------------------


def test_load_missing_file_returns_empty(tmp_path: Path):
    assert load_project_overrides(tmp_path / "nope.yaml") == {}


def test_load_well_formed_yaml(tmp_path: Path):
    p = tmp_path / "projects.yaml"
    p.write_text(
        """
projects:
  myproject:
    path_prefixes:
      - myproject/
  monorepo-shared:
    path_prefixes:
      - apps/billing/shared/
      - libs/shared/
""",
        encoding="utf-8",
    )
    out = load_project_overrides(p)
    assert out == {
        "myproject": ["myproject/"],
        "monorepo-shared": ["apps/billing/shared/", "libs/shared/"],
    }


def test_load_skips_malformed_entries(tmp_path: Path):
    p = tmp_path / "projects.yaml"
    p.write_text(
        """
projects:
  good:
    path_prefixes: [good/]
  bad_list:
    path_prefixes: not_a_list
  bad_value: "i'm a string, not a dict"
""",
        encoding="utf-8",
    )
    out = load_project_overrides(p)
    assert out == {"good": ["good/"]}


def test_load_returns_empty_on_empty_file(tmp_path: Path):
    p = tmp_path / "projects.yaml"
    p.write_text("", encoding="utf-8")
    assert load_project_overrides(p) == {}


def test_load_returns_empty_when_projects_key_missing(tmp_path: Path):
    p = tmp_path / "projects.yaml"
    p.write_text("foo: bar\n", encoding="utf-8")
    assert load_project_overrides(p) == {}


def test_load_drops_empty_string_prefixes(tmp_path: Path):
    p = tmp_path / "projects.yaml"
    p.write_text(
        """
projects:
  x:
    path_prefixes: ["", "  ", "x/"]
""",
        encoding="utf-8",
    )
    out = load_project_overrides(p)
    assert out == {"x": ["x/"]}
