"""Pluggable vector stores behind one async interface.

Backends (per the project's $0-verifiable-vs-AWS-only constraint):

  * **pgvector** — the primary, fully-tested-and-demoed backend. Runs in
    docker-compose and in CI as a ``pgvector/pgvector`` service container, and
    maps cleanly to RDS/Aurora in the cloud.
  * **FAISS** — a lightweight in-process alternate for unit tests (PR3).
  * **OpenSearch Serverless** — the cloud-native option, wired through the same
    interface and validated via ``terraform plan`` (PR3/PR11); not emulable at
    $0, so it is mock-tested rather than run locally.

PR0 ships pgvector only.
"""

from fossilrag.vectorstore.base import VectorStore

__all__ = ["VectorStore", "make_vector_store"]


async def make_vector_store(settings=None):  # noqa: ANN001, ANN201
    """Construct and bootstrap the vector store named by config.

    PR0 supports pgvector only. Returns a connected, schema-bootstrapped store.
    """
    from fossilrag.config import get_settings
    from fossilrag.vectorstore.pgvector import PgVectorStore

    settings = settings or get_settings()
    store = await PgVectorStore.connect(settings)
    await store.bootstrap()
    return store
