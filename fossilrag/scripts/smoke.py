"""End-to-end smoke test against a RUNNING FossilRAG API.

Used by the docker-compose CI job to prove the spine works through real HTTP:
ingest a document, then excavate a phrase from it and assert the fossil comes
back. Exits non-zero on any failure so CI fails loudly.

    python -m scripts.smoke [base_url]   # default http://localhost:8000
"""

from __future__ import annotations

import contextlib
import sys
import time

import httpx

DOC = (
    "Velociraptor was a small dromaeosaurid dinosaur.\n\n"
    "It lived during the Late Cretaceous period in what is now Mongolia.\n\n"
    "A distinctive sickle-shaped claw on each foot is its best-known feature."
)


def _wait_healthy(client: httpx.Client, attempts: int = 30) -> None:
    for _ in range(attempts):
        with contextlib.suppress(httpx.HTTPError):
            r = client.get("/healthz", timeout=5)
            if r.status_code == 200 and r.json().get("status") == "ok":
                return
        time.sleep(2)
    raise SystemExit(f"API not healthy after {attempts} attempts")


def main() -> None:
    base = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
    with httpx.Client(base_url=base) as client:
        _wait_healthy(client)

        r = client.post(
            "/ingest",
            json={"filename": "velociraptor.txt", "text": DOC},
            timeout=30,
        )
        r.raise_for_status()
        ingested = r.json()
        assert ingested["chunks_indexed"] >= 3, ingested
        print(
            f"smoke: ingested doc_id={ingested['doc_id'][:12]} chunks={ingested['chunks_indexed']}"
        )

        r = client.get("/excavate", params={"q": "sickle-shaped claw on each foot", "k": 3})
        r.raise_for_status()
        hits = r.json()["hits"]
        assert hits, "no hits returned"
        joined = " ".join(h["content"] for h in hits)
        assert "sickle" in joined, f"expected fossil not retrieved: {joined!r}"
        print(f"smoke: excavated {len(hits)} hits, top score={hits[0]['score']:.3f}")

    print("SMOKE OK")


if __name__ == "__main__":
    main()
