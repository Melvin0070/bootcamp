"""AWS glue for local orchestration: endpoint-aware clients + resource bootstrap.

Used by the LocalStack demo (Component 6) to stand up — at $0, against
LocalStack rather than a real account — the same topology Terraform deploys to
AWS (Component 5): the medallion buckets, the ingest queue + DLQ, and the
DynamoDB ledger/cache tables. ``boto3`` is an optional (``aws`` extra)
dependency imported lazily, so importing this package never requires it; the
testable core takes injected clients (driven by ``moto`` in CI).
"""

from fossilrag.aws.bootstrap import ResourceNames, provision
from fossilrag.aws.clients import make_client

__all__ = ["make_client", "provision", "ResourceNames"]
