from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from sentence_transformers import SentenceTransformer

QueryTask = Literal["search", "code"]


@dataclass(frozen=True)
class EmbeddingProfile:
    """How a model wants its inputs framed. Unknown models get the empty profile:
    raw text on both sides, which is what MiniLM-style models expect."""

    # sentence-transformers prompt name per query task; None = no prompt.
    query_prompts: dict[str, str | None] = field(default_factory=dict)
    # Document framing; {title} and {text} are filled in. None = raw text.
    document_template: str | None = None
    max_seq_length: int | None = None
    config_kwargs: dict = field(default_factory=dict)


_PROFILES: dict[str, EmbeddingProfile] = {
    "google/embeddinggemma-2": EmbeddingProfile(
        query_prompts={"search": "SearchQuery", "code": "CodeRetrieval"},
        document_template="title: {title} | text: {text}",
        # The model accepts 8192 tokens; 512 keeps CPU indexing at ~8 chunks/s
        # and already doubles what MiniLM sees.
        max_seq_length=512,
        # Text-only: skip the vision and audio encoders (740M → 270M params).
        config_kwargs={"vision_config": None, "audio_config": None},
    ),
}

_model_cache: dict[tuple[str, str | None], SentenceTransformer] = {}


def _load(model_name: str, profile: EmbeddingProfile, device: str | None) -> SentenceTransformer:
    model = SentenceTransformer(model_name, device=device, config_kwargs=profile.config_kwargs or None)
    if profile.max_seq_length:
        model.max_seq_length = profile.max_seq_length
    # Weights published in bfloat16 run ~13x slower on CPU than float32.
    if model.device.type == "cpu":
        model = model.float()
    return model


class EmbeddingService:
    def __init__(self, model_name: str = "all-MiniLM-L6-v2", device: str | None = None) -> None:
        self.model_name = model_name
        self._profile = _PROFILES.get(model_name, EmbeddingProfile())
        key = (model_name, device)
        if key not in _model_cache:
            _model_cache[key] = _load(model_name, self._profile, device)
        self._model = _model_cache[key]

    @property
    def dimension(self) -> int:
        return self._model.get_embedding_dimension()

    def embed_query(self, text: str, task: QueryTask = "search") -> list[float]:
        """Embed what a user asked. `task="code"` when the target is source code."""
        prompt = self._profile.query_prompts.get(task)
        vector = self._model.encode(text, prompt_name=prompt, normalize_embeddings=True)
        return vector.tolist()

    def embed_documents(
        self, texts: list[str], titles: list[str] | None = None
    ) -> list[list[float]]:
        """Embed indexed content. `titles` (e.g. file paths) only matter for
        models whose profile frames documents with one."""
        template = self._profile.document_template
        if template:
            texts = [
                template.format(title=(titles[i] if titles else None) or "none", text=t)
                for i, t in enumerate(texts)
            ]
        vectors = self._model.encode(texts, normalize_embeddings=True)
        return [v.tolist() for v in vectors]
