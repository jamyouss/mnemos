"""Initialize Qdrant collections and prepare for indexing."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from qdrant_client import QdrantClient

# Add packages directory to path so core is importable
sys.path.insert(0, str(Path(__file__).parent.parent / "packages"))

from core.collections import COLLECTIONS
from core.indexer import TAGGED_COLLECTIONS, create_hybrid_collection


def init_collections(client: QdrantClient, vector_size: int) -> None:
    """Create Qdrant collections if they don't already exist.

    Args:
        client: QdrantClient instance connected to Qdrant server.
        vector_size: dense size of the configured embedding model.
    """
    for config in COLLECTIONS:
        if client.collection_exists(config.name):
            print(f"  Collection '{config.name}' already exists")
            continue
        create_hybrid_collection(
            client, config.name, vector_size, config.name in TAGGED_COLLECTIONS
        )
        print(f"  Created collection '{config.name}'")


def main():
    """Connect to Qdrant and initialize all collections."""
    host = os.getenv("QDRANT_HOST", "localhost")
    port = int(os.getenv("QDRANT_PORT", "6333"))

    print(f"Connecting to Qdrant at {host}:{port}...")
    client = QdrantClient(host=host, port=port)

    print("Initializing collections...")
    init_collections(client, int(os.getenv("EMBEDDING_DIM", "384")))

    print("Done! Collections are ready for indexing.")


if __name__ == "__main__":
    main()
