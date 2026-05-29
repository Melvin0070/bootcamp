"""Demo the serverless ingestion loop end-to-end on LocalStack (the `aws` profile).

Half 1 of the local demo (half 2 is `scripts.smoke`: POST /ingest → GET
/excavate over pgvector). This drives the decoupled, burst-absorbing ingestion
path exactly as deployed:

    upload to raw S3  →  S3 ObjectCreated  →  SQS  →  worker  →  silver S3

…then re-uploads the same objects to show **Self-Healing Idempotency**: the
worker's DynamoDB ledger skips already-processed objects, so the redelivered
events are no-ops (no new silver writes).

Run it inside the stack so it shares the compose network + LocalStack endpoint:

    make demo            # docker compose --profile aws run --rm worker python -m scripts.demo_aws
"""

from __future__ import annotations

import sys
import time

from fossilrag.aws import ResourceNames, make_client
from fossilrag.config import get_settings
from fossilrag.logging import configure_logging

DOCS: dict[str, str] = {
    "digs/site-alpha.txt": (
        "Site Alpha yielded a near-complete Velociraptor skeleton.\n\n"
        "The sickle-shaped claw was recovered intact from the Late Cretaceous layer."
    ),
    "digs/site-beta.txt": (
        "Site Beta produced a clutch of fossilised theropod eggs.\n\n"
        "Sediment dating places the nest in the Campanian stage, roughly 75 Ma."
    ),
}


def _silver_objects(s3, bucket: str, prefix: str) -> dict:  # noqa: ANN001
    """Map each silver object key → its S3 LastModified timestamp."""
    resp = s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
    return {o["Key"]: o["LastModified"] for o in resp.get("Contents", [])}


def _silver_keys(s3, bucket: str, prefix: str) -> set[str]:  # noqa: ANN001
    return set(_silver_objects(s3, bucket, prefix))


def _wait_for_silver(s3, bucket: str, prefix: str, want: int, attempts: int = 30) -> set[str]:  # noqa: ANN001
    for _ in range(attempts):
        keys = _silver_keys(s3, bucket, prefix)
        if len(keys) >= want:
            return keys
        time.sleep(2)
    raise SystemExit(f"timed out waiting for {want} silver objects (got {len(keys)})")


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    names = ResourceNames.from_settings(settings)
    s3 = make_client("s3", settings)

    print(f"→ uploading {len(DOCS)} documents to raw bucket {names.raw_bucket!r}")
    for key, text in DOCS.items():
        s3.put_object(
            Bucket=names.raw_bucket, Key=key, Body=text.encode("utf-8"), ContentType="text/plain"
        )

    print("→ raw → SQS → worker → silver … (decoupled, burst-absorbing)")
    silver = _wait_for_silver(s3, names.silver_bucket, settings.silver_prefix, len(DOCS))
    print(f"✓ {len(silver)} objects landed in silver bucket {names.silver_bucket!r}:")
    for k in sorted(silver):
        print(f"    {k}")

    # Show one silver record so the extraction is visible, not just counted.
    sample = s3.get_object(Bucket=names.silver_bucket, Key=sorted(silver)[0])["Body"].read()
    print(
        f"  sample silver record ({len(sample)} bytes): {sample[:120].decode('utf-8', 'replace')}…"
    )

    # Capture LastModified BEFORE the re-drop. This is the discriminating signal:
    # the silver key is content-addressed, so re-uploading identical bytes leaves
    # the key SET unchanged whether or not the ledger skips — but if the worker
    # actually skips (idempotency working) it makes zero S3 writes, so silver
    # LastModified is FROZEN. If the ledger were off, the worker would overwrite
    # silver and LastModified would advance. So unchanged timestamps prove the skip.
    before = _silver_objects(s3, names.silver_bucket, settings.silver_prefix)

    print("\n→ re-uploading the SAME objects to exercise self-healing idempotency")
    for key, text in DOCS.items():
        s3.put_object(
            Bucket=names.raw_bucket, Key=key, Body=text.encode("utf-8"), ContentType="text/plain"
        )
    time.sleep(6)  # let the worker drain + skip the redelivered events
    after = _silver_objects(s3, names.silver_bucket, settings.silver_prefix)
    assert set(after) == silver, f"idempotency broken: silver keys changed {silver} -> {set(after)}"
    unchanged = {k: after[k] for k in before} == before
    assert unchanged, "idempotency broken: silver was re-written (LastModified advanced on re-drop)"
    print(
        f"✓ no new silver writes — still {len(after)} objects, LastModified frozen (worker skipped)"
    )

    if settings.idempotency_backend == "dynamodb" and settings.idempotency_table:
        from fossilrag.idempotency import DynamoDBStore

        stats = DynamoDBStore(
            settings.idempotency_table, client=make_client("dynamodb", settings)
        ).stats()
        print(f"  idempotency ledger: processed={stats['processed']} pending={stats['pending']}")

    print("\nDEMO OK — S3 → SQS → worker → silver, with self-healing idempotency.")
    print("Now run the retrieval half:  make smoke   (POST /ingest → GET /excavate over pgvector)")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print(f"DEMO FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
