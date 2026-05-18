from __future__ import annotations

from pathlib import Path

import pytest

from core.projects import detect_tags, load_path_tags


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


# ---------------------------------------------------------------------------
# load_path_tags — new schema
# ---------------------------------------------------------------------------


def test_load_path_tags_missing_file(tmp_path: Path):
    assert load_path_tags(tmp_path / "nope.yaml") == {}


def test_load_path_tags_new_schema(tmp_path: Path):
    p = tmp_path / "projects.yaml"
    p.write_text(
        """
paths:
  Projects/acme-corp/acme/front/applications/ecommerce/:
    - acme-front-app-ecommerce
    - acme
    - acme-front
    - acme-corp

  Projects/digital-gigafactory/moby/services/:
    - moby-services
    - moby
    - dgf
""",
        encoding="utf-8",
    )
    out = load_path_tags(p)
    assert out == {
        "Projects/acme-corp/acme/front/applications/ecommerce/": [
            "acme-front-app-ecommerce", "acme", "acme-front", "acme-corp",
        ],
        "Projects/digital-gigafactory/moby/services/": [
            "moby-services", "moby", "dgf",
        ],
    }


def test_load_path_tags_legacy_schema_auto_converts(tmp_path: Path, caplog):
    """The legacy `projects:` schema must keep working — we convert it
    in-memory to the new path → [project_name] shape and emit a warning."""
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
    with caplog.at_level("WARNING"):
        out = load_path_tags(p)
    assert out == {
        "myproject/":            ["myproject"],
        "apps/billing/shared/":  ["monorepo-shared"],
        "libs/shared/":          ["monorepo-shared"],
    }
    assert any("legacy" in rec.message.lower() for rec in caplog.records)


def test_load_path_tags_empty_file_returns_empty(tmp_path: Path):
    p = tmp_path / "projects.yaml"
    p.write_text("", encoding="utf-8")
    assert load_path_tags(p) == {}


def test_load_path_tags_drops_malformed_entries(tmp_path: Path, caplog):
    p = tmp_path / "projects.yaml"
    p.write_text(
        """
paths:
  good/path/:
    - tag-a
    - tag-b
  bad-not-a-list/: "just a string"
  empty/path/: []
""",
        encoding="utf-8",
    )
    with caplog.at_level("WARNING"):
        out = load_path_tags(p)
    assert out == {"good/path/": ["tag-a", "tag-b"]}


def test_load_path_tags_strips_blank_tags(tmp_path: Path):
    p = tmp_path / "projects.yaml"
    p.write_text(
        """
paths:
  x/:
    - ""
    - "  "
    - real-tag
""",
        encoding="utf-8",
    )
    assert load_path_tags(p) == {"x/": ["real-tag"]}
