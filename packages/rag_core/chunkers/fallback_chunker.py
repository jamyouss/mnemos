from __future__ import annotations


class FallbackChunker:
    def __init__(self, max_tokens: int = 500, overlap_tokens: int = 50) -> None:
        self.max_tokens = max_tokens
        self.overlap_tokens = overlap_tokens

    def chunk(self, content: str, file_path: str) -> list[dict]:
        words = content.split()
        if len(words) <= self.max_tokens:
            return [
                {
                    "content": content,
                    "file_path": file_path,
                    "chunk_type": "file",
                    "chunk_index": 0,
                }
            ]

        chunks = []
        start = 0
        index = 0
        while start < len(words):
            end = min(start + self.max_tokens, len(words))
            chunk_words = words[start:end]
            chunks.append(
                {
                    "content": " ".join(chunk_words),
                    "file_path": file_path,
                    "chunk_type": "window",
                    "chunk_index": index,
                }
            )
            start = end - self.overlap_tokens
            index += 1
            if end == len(words):
                break

        return chunks
