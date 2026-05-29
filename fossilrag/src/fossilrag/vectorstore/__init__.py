"""Pluggable vector stores behind one async interface.

Backends (per the project's $0-verifiable-vs-AWS-only constraint):

  * **pgvector** — the primary, fully-tested-and-demoed backend, and the **only
    shipped** ``VectorStore``. Runs in docker-compose and in CI as a
    ``pgvector/pgvector`` service container, and maps cleanly to RDS/Aurora.
  * **OpenSearch Serverless** — the cloud-native option: IaC-provisioned
    (``infra/opensearch.tf``, ``terraform plan``-validated) but **not yet bound**
    behind this interface; an ``OpenSearchStore`` impl is the documented next
    step (see ADR 0001). FAISS is a candidate in-process backend, not yet built.

``make_vector_store`` supports pgvector only.
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
