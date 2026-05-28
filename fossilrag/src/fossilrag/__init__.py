"""FossilRAG — serverless document enrichment & retrieval.

The Dinosaur Whisperer's excavation engine: raw documents are *ingested*,
cleaned and split into semantic *fossil layers* (chunks), *embedded* into a
vector space, and made searchable through a FastAPI surface (``/excavate``
top-k retrieval, ``/mutate`` LLM summary/edit).

This package is built walking-skeleton-first: the spine (ingest → chunk →
embed → store → retrieve) runs end-to-end at trivial fidelity from day one,
and each component is deepened in its own pull request while ``main`` stays
demoable.
"""

__version__ = "0.1.0"
