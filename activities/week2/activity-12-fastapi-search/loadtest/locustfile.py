"""
Locust load test for /search.

Run against the broken baseline AND the fixed app to see the before/after.

    # before: start broken/app.py on :8000, then
    locust -f loadtest/locustfile.py --host=http://localhost:8000 \\
           --users 50 --spawn-rate 10 --run-time 30s --headless

    # after: start app.py on :8000, then re-run the same command.

Expected delta on the 500 k-row fixture (see scripts/seed_pg.py):

    Broken:
        median ~6300 ms, p95 ~9800 ms, ~30% timeouts at 50 concurrent
    Fixed:
        median ~8 ms,    p95 ~19 ms,   0 timeouts at 50 concurrent
        median ~18 ms,   p95 ~41 ms,   0 timeouts at 200 concurrent
"""

import random

from locust import HttpUser, between, task

QUERIES = [
    "stego",
    "trex",
    "raptor",
    "tricer",
    "brachio",
    "ankylo",
    "diplodo",
    "ptero",
    "spinosaur",
    "allosaur",
    "iguanodon",
    "parasaur",
    "carnotaur",
    "thero",
    "sauropod",
]


class SearchUser(HttpUser):
    # Short think-time keeps the load high.
    wait_time = between(0.1, 0.3)

    @task(10)
    def search(self):
        q = random.choice(QUERIES)
        with self.client.get(
            f"/search?q={q}&limit=10",
            name="/search",
            catch_response=True,
        ) as resp:
            # The broken baseline sometimes returns 500s under load
            # (Postgres out of connections). Mark them as failures so
            # they show up in the locust report.
            if resp.status_code != 200:
                resp.failure(f"status={resp.status_code} body={resp.text[:200]}")

    @task(1)
    def health(self):
        # 10% of traffic is health-check, mirroring what a load
        # balancer would do. The broken baseline opens a fresh
        # connection per /healthz, which contributes to the outage
        # amplification.
        self.client.get("/healthz", name="/healthz")
