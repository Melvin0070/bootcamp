"""Pluggable embedding providers.

One :class:`~fossilrag.embedding.base.Embedder` interface, three backends:
the deterministic :class:`MockEmbedder` ($0 default), the local
sentence-transformers :class:`LocalEmbedder` (dev/compose fallback), and the
cloud :class:`BedrockEmbedder` (Titan v2). The real backends' heavy/cloud deps
are imported lazily, so importing this package never requires torch or boto3.
"""

from fossilrag.embedding.base import Embedder
from fossilrag.embedding.mock import MockEmbedder

__all__ = ["Embedder", "MockEmbedder", "make_embedder"]


def make_embedder(settings=None):  # noqa: ANN001, ANN201
    """Construct the embedder named by ``settings.embed_provider``.

    mock (default) | local (sentence-transformers) | bedrock (Titan v2). When
    ``embed_model`` is still the mock sentinel, the local/bedrock branches fall
    back to their own sensible default model.
    """
    from fossilrag.config import get_settings

    settings = settings or get_settings()
    provider = settings.embed_provider.lower()
    model = settings.embed_model

    if provider == "mock":
        return MockEmbedder(model_id=model, dimensions=settings.embed_dim)
    if provider == "local":
        from fossilrag.embedding.local import DEFAULT_MODEL, LocalEmbedder

        name = model if model and not model.startswith("mock") else DEFAULT_MODEL
        return LocalEmbedder(model_name=name)
    if provider == "bedrock":
        from fossilrag.embedding.bedrock import DEFAULT_MODEL, BedrockEmbedder

        # BedrockEmbedder speaks Titan v2's request/response shape only, so any
        # non-Titan id falls back to the Titan default rather than silently
        # sending a malformed request to e.g. a Cohere model.
        model_id = model if model.startswith("amazon.") else DEFAULT_MODEL
        return BedrockEmbedder(
            model_id=model_id, dimensions=settings.embed_dim, region=settings.aws_region
        )
    raise ValueError(f"Unknown embed_provider={provider!r} (mock|local|bedrock).")
