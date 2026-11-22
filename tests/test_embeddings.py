from types import SimpleNamespace

import numpy as np
import pytest

import core.embeddings as embeddings_module
from core.embeddings import EmbeddingService


def test_embed_query_single_text():
    service = EmbeddingService(model_name="all-MiniLM-L6-v2")
    result = service.embed_query("Hello world")
    assert len(result) == service.dimension == 384
    assert all(isinstance(v, float) for v in result)


def test_embed_documents_batch():
    service = EmbeddingService(model_name="all-MiniLM-L6-v2")
    results = service.embed_documents(["Hello", "World", "Test"], titles=["a", "b", "c"])
    assert len(results) == 3
    assert all(len(v) == 384 for v in results)


def test_embed_code_produces_different_vectors():
    service = EmbeddingService(model_name="all-MiniLM-L6-v2")
    v1 = service.embed_query("func Create(ctx context.Context)")
    v2 = service.embed_query("func Delete(ctx context.Context)")
    assert v1 != v2


def test_singleton_model_loading():
    """Model should be loaded once and reused."""
    s1 = EmbeddingService(model_name="all-MiniLM-L6-v2")
    s2 = EmbeddingService(model_name="all-MiniLM-L6-v2")
    assert s1._model is s2._model


class _FakeModel:
    """Records what reaches `encode`, so framing can be checked without weights."""

    def __init__(self, name, device=None, config_kwargs=None):
        self.config_kwargs = config_kwargs
        self.device = SimpleNamespace(type=device or "cpu")
        self.max_seq_length = 10**30
        self.floated = False
        self.calls: list[tuple] = []

    def float(self):
        self.floated = True
        return self

    def get_embedding_dimension(self):
        return 4

    def encode(self, inputs, prompt_name=None, normalize_embeddings=False):
        self.calls.append((inputs, prompt_name))
        if isinstance(inputs, str):
            return np.zeros(4)
        return np.zeros((len(inputs), 4))


@pytest.fixture
def fake_models(monkeypatch):
    monkeypatch.setattr(embeddings_module, "SentenceTransformer", _FakeModel)
    monkeypatch.setattr(embeddings_module, "_model_cache", {})


def test_unprofiled_model_sees_raw_text_on_both_sides(fake_models):
    service = EmbeddingService("some/plain-model")
    service.embed_query("where is auth", task="code")
    service.embed_documents(["func Auth()"], titles=["svc/auth.go"])
    assert service._model.calls == [("where is auth", None), (["func Auth()"], None)]


def test_embeddinggemma_frames_queries_per_task_and_titles_documents(fake_models):
    service = EmbeddingService("google/embeddinggemma-2")
    service.embed_query("where is auth", task="code")
    service.embed_query("how do we deploy")
    service.embed_documents(["func Auth()", "note"], titles=["svc/auth.go", ""])
    service.embed_documents(["memory text"])
    assert service._model.calls == [
        ("where is auth", "CodeRetrieval"),
        ("how do we deploy", "SearchQuery"),
        (["title: svc/auth.go | text: func Auth()", "title: none | text: note"], None),
        (["title: none | text: memory text"], None),
    ]


def test_embeddinggemma_loads_text_only_and_capped(fake_models):
    model = EmbeddingService("google/embeddinggemma-2")._model
    assert model.config_kwargs == {"vision_config": None, "audio_config": None}
    assert model.max_seq_length == 512


def test_cpu_weights_are_cast_to_float32(fake_models):
    """bfloat16 weights run ~13x slower than float32 on CPU."""
    assert EmbeddingService("google/embeddinggemma-2", device="cpu")._model.floated
    assert not EmbeddingService("google/embeddinggemma-2", device="mps")._model.floated
