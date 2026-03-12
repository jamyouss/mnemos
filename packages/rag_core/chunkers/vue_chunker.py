from __future__ import annotations

import re


class VueChunker:
    _block_re = re.compile(
        r"<(template|script|style)(\s[^>]*)?>(.+?)</\1>",
        re.DOTALL,
    )

    def chunk(self, content: str, file_path: str) -> list[dict]:
        matches = self._block_re.findall(content)
        if not matches:
            return [
                {
                    "content": content,
                    "file_path": file_path,
                    "chunk_type": "file",
                    "language": "vue",
                    "chunk_index": 0,
                }
            ]

        chunks = []
        for i, (tag, _attrs, block_content) in enumerate(matches):
            chunks.append(
                {
                    "content": block_content.strip(),
                    "file_path": file_path,
                    "chunk_type": tag,
                    "language": "vue",
                    "chunk_index": i,
                }
            )

        return chunks
