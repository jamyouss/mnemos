"""Initialize Qdrant collections and prepare for indexing."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from qdrant_client import QdrantClient

# Add packages directory to path so rag_core is importable
sys.path.insert(0, str(Path(__file__).parent.parent / "packages"))

from rag_core.collections import COLLECTIONS


def init_collections(client: QdrantClient) -> None:
    """Create Qdrant collections if they don't already exist.

    Args:
        client: QdrantClient instance connected to Qdrant server.
    """
    for config in COLLECTIONS:
        try:
            client.get_collection(config.name)
            print(f"  Collection '{config.name}' already exists")
        except Exception:
            from qdrant_client.models import Distance, VectorParams

            client.create_collection(
                collection_name=config.name,
                vectors_config=VectorParams(
                    size=config.vector_size, distance=Distance.COSINE
                ),
            )
            print(f"  Created collection '{config.name}'")


def main():
    """Connect to Qdrant and initialize all collections."""
    host = os.getenv("QDRANT_HOST", "localhost")
    port = int(os.getenv("QDRANT_PORT", "6333"))

    print(f"Connecting to Qdrant at {host}:{port}...")
    client = QdrantClient(host=host, port=port)

    print("Initializing collections...")
    init_collections(client)

    print("Done! Collections are ready for indexing.")


if __name__ == "__main__":
    main()
