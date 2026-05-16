from __future__ import annotations

import tree_sitter_go as tsgo
from tree_sitter import Language, Parser

from core.chunkers.fallback_chunker import FallbackChunker

GO_LANGUAGE = Language(tsgo.language())


class GoChunker:
    def __init__(self) -> None:
        self._parser = Parser(GO_LANGUAGE)
        self._fallback = FallbackChunker()

    def chunk(self, content: str, file_path: str) -> list[dict]:
        try:
            tree = self._parser.parse(bytes(content, "utf-8"))
            root = tree.root_node

            # If the tree has errors and no useful top-level nodes, fall back.
            if root.has_error and not self._has_parseable_nodes(root):
                return self._fallback_chunks(content, file_path)

            return self._extract_chunks(root, content, file_path)
        except Exception:
            return self._fallback_chunks(content, file_path)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _has_parseable_nodes(self, root) -> bool:
        useful_types = {
            "package_clause",
            "import_declaration",
            "function_declaration",
            "method_declaration",
            "type_declaration",
        }
        return any(child.type in useful_types for child in root.children)

    def _fallback_chunks(self, content: str, file_path: str) -> list[dict]:
        chunks = self._fallback.chunk(content, file_path)
        for chunk in chunks:
            chunk.setdefault("language", "go")
            chunk.setdefault("symbol_name", "")
        return chunks

    def _extract_chunks(self, root, content: str, file_path: str) -> list[dict]:
        chunks: list[dict] = []
        lines = content.splitlines(keepends=True)

        header_lines: list[str] = []
        header_done = False
        chunk_index = 0

        for child in root.children:
            node_type = child.type

            if node_type in ("package_clause", "import_declaration") and not header_done:
                # Collect leading comments that immediately precede this node
                # (they're already emitted into header_lines below)
                start = child.start_point[0]
                end = child.end_point[0] + 1
                header_lines.extend(lines[start:end])
                continue

            if node_type == "comment" and not header_done:
                # Comments before any declarations go into the header
                start = child.start_point[0]
                end = child.end_point[0] + 1
                header_lines.extend(lines[start:end])
                continue

            # Once we hit a non-header node, flush the header chunk
            if not header_done and header_lines:
                header_content = "".join(header_lines).rstrip()
                chunks.append(
                    {
                        "content": header_content,
                        "file_path": file_path,
                        "chunk_type": "header",
                        "symbol_name": "",
                        "language": "go",
                        "chunk_index": chunk_index,
                    }
                )
                chunk_index += 1
                header_done = True

            if node_type in ("function_declaration", "method_declaration"):
                symbol_name = self._extract_function_name(child)
                node_content = self._node_text(child, content)
                chunks.append(
                    {
                        "content": node_content,
                        "file_path": file_path,
                        "chunk_type": "function",
                        "symbol_name": symbol_name,
                        "language": "go",
                        "chunk_index": chunk_index,
                    }
                )
                chunk_index += 1

            elif node_type == "type_declaration":
                symbol_name = self._extract_type_name(child)
                node_content = self._node_text(child, content)
                chunks.append(
                    {
                        "content": node_content,
                        "file_path": file_path,
                        "chunk_type": "type",
                        "symbol_name": symbol_name,
                        "language": "go",
                        "chunk_index": chunk_index,
                    }
                )
                chunk_index += 1

        # Flush header if no declarations were found after it
        if not header_done and header_lines:
            header_content = "".join(header_lines).rstrip()
            chunks.append(
                {
                    "content": header_content,
                    "file_path": file_path,
                    "chunk_type": "header",
                    "symbol_name": "",
                    "language": "go",
                    "chunk_index": chunk_index,
                }
            )

        if not chunks:
            return self._fallback_chunks(content, file_path)

        return chunks

    def _node_text(self, node, content: str) -> str:
        return content[node.start_byte:node.end_byte]

    def _extract_function_name(self, node) -> str:
        """Extract the function/method name from a declaration node."""
        for child in node.children:
            if child.type == "field_identifier":
                # method_declaration: receiver + field_identifier as method name
                return child.text.decode("utf-8")
            if child.type == "identifier":
                # function_declaration: identifier is the function name
                return child.text.decode("utf-8")
        return ""

    def _extract_type_name(self, node) -> str:
        """Extract the type name from a type_declaration node."""
        for child in node.children:
            if child.type == "type_spec":
                for spec_child in child.children:
                    if spec_child.type == "type_identifier":
                        return spec_child.text.decode("utf-8")
        return ""
