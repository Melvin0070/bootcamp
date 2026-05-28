"""
Seed a 500 k-row specimens table for the load test.

    psql "$DATABASE_URL" -f migrations/0000_schema.sql
    python scripts/seed_pg.py --rows 500000

Idempotent on TRUNCATE: re-running rebuilds the table without
duplicating rows. Use --keep-existing to skip the truncate (e.g. for
appending to an existing dataset).
"""

from __future__ import annotations

import argparse
import os
import random
import sys

import psycopg2
from psycopg2.extras import execute_values

GENERA = [
    "stegosaurus",
    "tyrannosaurus",
    "velociraptor",
    "triceratops",
    "brachiosaurus",
    "ankylosaurus",
    "diplodocus",
    "pterodactyl",
    "spinosaurus",
    "allosaurus",
    "iguanodon",
    "parasaurolophus",
    "carnotaurus",
    "therizinosaurus",
    "argentinosaurus",
]
SUFFIXES = ["rex", "minor", "major", "australis", "americanus", "borealis"]


def gen_species(rng: random.Random) -> str:
    return f"{rng.choice(GENERA)} {rng.choice(SUFFIXES)}"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--rows", type=int, default=500_000)
    p.add_argument("--batch", type=int, default=10_000)
    p.add_argument("--keep-existing", action="store_true")
    p.add_argument("--seed", type=int, default=42)
    args = p.parse_args()

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL not set", file=sys.stderr)
        return 1

    rng = random.Random(args.seed)
    conn = psycopg2.connect(dsn)
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            if not args.keep_existing:
                cur.execute("TRUNCATE specimens RESTART IDENTITY")
            written = 0
            while written < args.rows:
                n = min(args.batch, args.rows - written)
                values = [
                    (
                        gen_species(rng),
                        round(rng.uniform(65, 250), 2),
                        f"specimen #{written + i}",
                    )
                    for i in range(n)
                ]
                execute_values(
                    cur,
                    "INSERT INTO specimens (species, age_mya, notes) VALUES %s",
                    values,
                )
                written += n
                print(f"  inserted {written:>9,}/{args.rows:,}")
        conn.commit()
    finally:
        conn.close()
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
