"""
FastAPI /search — BROKEN baseline.

This is the "before" state for Activity 12. The endpoint does what it
says on the tin: takes a query string, looks up matching fossil
specimens by species name, returns the top N. Under three concurrent
users on a developer laptop it returns in ~50 ms. Under twenty it
falls off a cliff. Under a hundred the event loop is hung, every
request times out at the gateway, and the on-call engineer's pager
fires.

Anti-patterns deliberately preserved
------------------------------------
1. **No connection pool.** Every request calls `psycopg2.connect(...)`,
   which is a synchronous TCP+TLS+auth handshake (~5–15 ms each).
   At 100 RPS that's 100 fresh connections per second, every one of
   which the database has to accept, fork a backend for, and tear
   down. Postgres `max_connections` is 100 by default — we hit it
   in two seconds.
2. **Synchronous DB calls inside an async endpoint.** `cursor.execute()`
   and `cursor.fetchall()` are blocking calls. Running them inside a
   coroutine blocks the FastAPI worker's event loop. Every other
   request on the same worker waits in line until the query returns.
   With one Uvicorn worker, "moderate traffic" means the second
   request is queued behind the first; concurrency is effectively 1.
3. **No database index on the search column.** The query does
   `WHERE species ILIKE '%...%'`. Without an index that is a full
   table scan. On a 500 k-row specimens table that's ~400 ms per
   query before any concurrency contention.
4. **No timeout.** A slow query holds the event loop indefinitely.
   A single bad query parameter can take the whole worker down.
5. **Connection string read from `os.environ` inline, in the request
   handler.** Every request re-parses it. Once a misconfiguration
   landed in prod and every request leaked the typo to the logs
   instead of failing at startup.
6. **No request validation.** `q` is interpolated straight into the
   SQL — classic SQL injection. The fact that the endpoint is also
   broken on performance has been hiding this from review.
"""

import os

import psycopg2
from fastapi import FastAPI

app = FastAPI()


@app.get("/search")
def search(q: str, limit: int = 10):
    # Anti-pattern: new connection per request, no pool. The TCP+TLS
    # handshake alone is the bulk of the latency at low concurrency,
    # and at high concurrency Postgres runs out of max_connections.
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    cur = conn.cursor()

    # Anti-pattern: SQL injection AND no index. The ILIKE with a
    # leading wildcard prevents btree index usage even if one existed;
    # the f-string interpolation means the query parameter is
    # concatenated straight into the SQL.
    sql = f"""
        SELECT id, species, age_mya, notes
        FROM specimens
        WHERE species ILIKE '%{q}%'
        LIMIT {limit}
    """
    cur.execute(sql)
    rows = cur.fetchall()

    cur.close()
    conn.close()

    return {
        "query": q,
        "results": [
            {"id": r[0], "species": r[1], "age_mya": r[2], "notes": r[3]}
            for r in rows
        ],
    }


@app.get("/healthz")
def healthz():
    # Anti-pattern: the healthcheck also opens a fresh connection.
    # When Postgres is at max_connections, the load balancer's health
    # check fails, the LB rotates the instance out, and the queue
    # grinds to a halt. Outage amplification.
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    conn.close()
    return {"status": "ok"}
