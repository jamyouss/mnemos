from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / "scripts" / "reindex-all.py"


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
