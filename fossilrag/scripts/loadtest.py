"""Lightweight async load test against a running FossilRAG API.

Fires N concurrent clients at ``/excavate`` (the hot retrieval path) and reports
latency percentiles + throughput. No external load tool — just httpx + asyncio,
so it runs anywhere the dev deps are installed.

    python -m scripts.loadtest [base_url] [--requests 500] [--concurrency 20] [--q "..."]

The stats core (:func:`summarise`) is pure and unit-tested; the HTTP driver is
run manually / in a load-test stage (not part of the unit gate).
"""

from __future__ import annotations

import argparse
import asyncio
import math
import statistics
import sys
import time

DEFAULT_QUERY = "late cretaceous apex predator"


def _percentile(sorted_ms: list[float], p: float) -> float:
    """Nearest-rank percentile of an already-sorted list (empty → 0.0)."""
    if not sorted_ms:
        return 0.0
    rank = max(1, math.ceil((p / 100.0) * len(sorted_ms)))
    return sorted_ms[rank - 1]


def summarise(latencies_ms: list[float]) -> dict[str, float]:
    """Latency summary (count + min/mean/p50/p90/p95/p99/max) in milliseconds."""
    if not latencies_ms:
        return dict.fromkeys(("count", "min", "mean", "p50", "p90", "p95", "p99", "max"), 0.0)
    s = sorted(latencies_ms)
    return {
        "count": float(len(s)),
        "min": s[0],
        "mean": statistics.fmean(s),
        "p50": _percentile(s, 50),
        "p90": _percentile(s, 90),
        "p95": _percentile(s, 95),
        "p99": _percentile(s, 99),
        "max": s[-1],
    }


async def _run(  # pragma: no cover - HTTP driver, exercised against a live API
    base_url: str, path: str, total: int, concurrency: int
) -> tuple[list[float], int]:
    import httpx

    latencies: list[float] = []
    errors = 0
    sem = asyncio.Semaphore(concurrency)

    async with httpx.AsyncClient(base_url=base_url, timeout=30) as client:

        async def one() -> None:
            nonlocal errors
            async with sem:
                t0 = time.perf_counter()
                try:
                    r = await client.get(path)
                    r.raise_for_status()
                except Exception:
                    errors += 1
                    return
                latencies.append((time.perf_counter() - t0) * 1000)

        await asyncio.gather(*(one() for _ in range(total)))
    return latencies, errors


def main() -> None:  # pragma: no cover - CLI entrypoint
    parser = argparse.ArgumentParser(description="FossilRAG load test")
    parser.add_argument("base_url", nargs="?", default="http://localhost:8000")
    parser.add_argument("--requests", type=int, default=500)
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--q", default=DEFAULT_QUERY)
    parser.add_argument("--k", type=int, default=5)
    args = parser.parse_args()

    path = f"/excavate?q={args.q.replace(' ', '+')}&k={args.k}"
    wall_start = time.perf_counter()
    latencies, errors = asyncio.run(_run(args.base_url, path, args.requests, args.concurrency))
    wall_s = time.perf_counter() - wall_start

    stats = summarise(latencies)
    ok = len(latencies)
    rps = ok / wall_s if wall_s else 0.0
    print(f"requests={args.requests} ok={ok} errors={errors} concurrency={args.concurrency}")
    print(f"throughput={rps:.1f} req/s  wall={wall_s:.2f}s")
    print(
        "latency_ms "
        + "  ".join(f"{k}={stats[k]:.1f}" for k in ("p50", "p90", "p95", "p99", "max"))
    )
    # Fail loudly if a meaningful fraction errored, so it's usable as a gate.
    if errors > max(0, args.requests // 100):
        print(f"LOADTEST FAILED: {errors} errors", file=sys.stderr)
        raise SystemExit(1)
    print("LOADTEST OK")


if __name__ == "__main__":
    main()
