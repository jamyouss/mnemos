from __future__ import annotations

from sentence_transformers import SentenceTransformer

_model_cache: dict[str, SentenceTransformer] = {}


class EmbeddingService:
    def __init__(self, model_name: str = "all-MiniLM-L6-v2") -> None:
        if model_name not in _model_cache:
            _model_cache[model_name] = SentenceTransformer(model_name)
        self._model = _model_cache[model_name]

    def embed(self, text: str) -> list[float]:
        vector = self._model.encode(text, normalize_embeddings=True)
        return vector.tolist()

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        vectors = self._model.encode(texts, normalize_embeddings=True)
        return [v.tolist() for v in vectors]
