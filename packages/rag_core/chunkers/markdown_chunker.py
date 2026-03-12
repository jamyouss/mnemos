from __future__ import annotations

import re


class MarkdownChunker:
    def chunk(self, content: str, file_path: str) -> list[dict]:
        lines = content.split("\n")

        doc_title = ""
        for line in lines:
            if line.startswith("# ") and not line.startswith("## "):
                doc_title = line.lstrip("# ").strip()
                break

        sections: list[tuple[str, list[str]]] = []
        current_header = ""
        current_lines: list[str] = []

        for line in lines:
            if re.match(r"^## ", line):
                if current_lines and current_header:
                    sections.append((current_header, current_lines))
                current_header = line.lstrip("# ").strip()
                current_lines = [line]
            elif current_header:
                current_lines.append(line)

        if current_lines and current_header:
            sections.append((current_header, current_lines))

        if not sections:
            return [
                {
                    "content": content,
                    "file_path": file_path,
                    "chunk_type": "file",
                    "chunk_index": 0,
                    "section": "",
                    "doc_title": doc_title,
                }
            ]

        return [
            {
                "content": "\n".join(section_lines).strip(),
                "file_path": file_path,
                "chunk_type": "section",
                "chunk_index": i,
                "section": header,
                "doc_title": doc_title,
            }
            for i, (header, section_lines) in enumerate(sections)
        ]
