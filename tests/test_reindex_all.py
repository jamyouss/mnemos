from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path
from urllib import error

SCRIPT = Path(__file__).parent.parent / "scripts" / "reindex-all.py"

PROJECTS = {
    "revision": 3,
    "writable": True,
    "paths": [
        {"prefix": "acme/app/", "tags": ["acme-app", "acme"], "exclude_dirs": [], "exclude_exts": []},
        {"prefix": "acme/reports", "tags": ["acme-reports"], "exclude_dirs": ["exports"], "exclude_exts": [".csv"]},
    ],
    "problems": [],
}


def _load():
    spec = importlib.util.spec_from_file_location("reindex_all", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_post_json_sends_the_api_key(monkeypatch):
    module = _load()
    seen = []

    def fake_urlopen(req, timeout):
        seen.append(req)
        return io.BytesIO(json.dumps({"status": "ok"}).encode())

    monkeypatch.setenv("MNEMOS_API_KEY", "secret-a")
    monkeypatch.setattr(module.request, "urlopen", fake_urlopen)

    module.post_json("http://mnemos.test/api/reindex", {"collection": "mnemos_code"})

    assert seen[0].get_header("Authorization") == "Bearer secret-a"


def test_load_paths_reads_the_projects_from_the_server(monkeypatch):
    module = _load()
    seen = []

    def fake_urlopen(req, timeout):
        seen.append(req)
        return io.BytesIO(json.dumps(PROJECTS).encode())

    monkeypatch.setenv("MNEMOS_API_KEY", "secret-a")
    monkeypatch.setattr(module.request, "urlopen", fake_urlopen)

    paths = module.load_paths("http://mnemos.test")

    assert paths == {"acme/app": ["acme-app", "acme"], "acme/reports": ["acme-reports"]}
    assert seen[0].full_url == "http://mnemos.test/api/projects"
    assert seen[0].get_header("Authorization") == "Bearer secret-a"


def test_main_fails_when_the_projects_cannot_be_read(monkeypatch):
    module = _load()

    def fake_urlopen(req, timeout):
        raise error.HTTPError(req.full_url, 403, "Forbidden", {}, None)

    monkeypatch.setattr(module.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(sys, "argv", [
        "reindex-all.py", "--mnemos-url", "http://mnemos.test", "--dry-run", "--skip-skills", "--skip-docs",
    ])

    assert module.main() == 2
