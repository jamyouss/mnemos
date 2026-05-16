from core.embeddings import EmbeddingService


def test_embed_single_text():
    service = EmbeddingService(model_name="all-MiniLM-L6-v2")
    result = service.embed("Hello world")
    assert len(result) == 384
    assert all(isinstance(v, float) for v in result)


def test_embed_batch():
    service = EmbeddingService(model_name="all-MiniLM-L6-v2")
    results = service.embed_batch(["Hello", "World", "Test"])
    assert len(results) == 3
    assert all(len(v) == 384 for v in results)


def test_embed_code_produces_different_vectors():
    service = EmbeddingService(model_name="all-MiniLM-L6-v2")
    v1 = service.embed("func Create(ctx context.Context)")
    v2 = service.embed("func Delete(ctx context.Context)")
    assert v1 != v2


def test_singleton_model_loading():
    """Model should be loaded once and reused."""
    s1 = EmbeddingService(model_name="all-MiniLM-L6-v2")
    s2 = EmbeddingService(model_name="all-MiniLM-L6-v2")
    assert s1._model is s2._model
