from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / "scripts" / "purge-filtered.py"


def _load():
    spec = importlib.util.spec_from_file_location("purge_filtered", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_project_rules_come_from_the_server(monkeypatch):
    module = _load()
    seen = []
    body = {
        "revision": 2,
        "writable": False,
        "paths": [
            {"prefix": "acme/app/", "tags": ["acme-app"], "exclude_dirs": [], "exclude_exts": []},
            {"prefix": "acme/reports/", "tags": ["acme-reports"], "exclude_dirs": ["exports"], "exclude_exts": [".csv"]},
        ],
        "problems": [],
    }

    def fake_urlopen(req, timeout):
        seen.append(req)
        return io.BytesIO(json.dumps(body).encode())

    monkeypatch.setenv("MNEMOS_API_KEY", "secret-b")
    monkeypatch.setattr(module.request, "urlopen", fake_urlopen)

    excludes, prefixes = module.project_rules("http://mnemos.test")

    assert excludes == {"acme/reports/": {"exts": [".csv"], "dirs": ["exports"]}}
    assert prefixes == ["acme/app/", "acme/reports/"]
    assert seen[0].full_url == "http://mnemos.test/api/projects"
    assert seen[0].get_header("Authorization") == "Bearer secret-b"
    # The rules feed the same skip decision the indexer makes.
    assert module.resolve_skip("/data/codebase/acme/reports/exports/a.md", "/data/codebase", excludes)
    assert not module.resolve_skip("/data/codebase/acme/app/main.go", "/data/codebase", excludes)
