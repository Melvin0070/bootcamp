"""Pluggable embedding providers.

The :class:`~fossilrag.embedding.base.Embedder` interface has one method
(``encode``) and two provenance properties (``model_id``, ``dimensions``).
PR0 ships the deterministic :class:`~fossilrag.embedding.mock.MockEmbedder`;
PR3 adds the local sentence-transformers fallback and the Bedrock Titan v2
cloud backend behind the same interface.
"""

from fossilrag.embedding.base import Embedder
from fossilrag.embedding.mock import MockEmbedder

__all__ = ["Embedder", "MockEmbedder", "make_embedder"]


def make_embedder(settings=None):  # noqa: ANN001, ANN201
    """Construct the embedder named by ``settings.embed_provider``.

    Only ``mock`` exists in PR0; ``local`` and ``bedrock`` are added in PR3.
    Kept as a factory so callers (API lifespan, pipeline scripts) never import
    a concrete backend directly.
    """
    from fossilrag.config import get_settings

    settings = settings or get_settings()
    provider = settings.embed_provider.lower()
    if provider == "mock":
        return MockEmbedder(model_id=settings.embed_model, dimensions=settings.embed_dim)
    raise ValueError(
        f"Unknown embed_provider={provider!r}. "
        "Supported in PR0: 'mock' (local/bedrock arrive in PR3)."
    )
