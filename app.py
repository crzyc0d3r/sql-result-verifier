"""Demonstrate why executable SQL is not necessarily correct SQL."""
import argparse
import json
from verifier import Verifier, fixture_snapshot

REFERENCE = "SELECT sum(cents) FROM sales WHERE region='west' AND day >= '2026-01-01' AND day < '2026-02-01'"
CANDIDATES = {
    "correct": REFERENCE,
    "equivalent": "SELECT sum(cents) FROM sales WHERE region='west' AND day LIKE '2026-01-%'",
    "wrong_region": "SELECT sum(cents) FROM sales WHERE region='east'",
    "missing_filter": "SELECT sum(cents) FROM sales",
    "duplicate_join": "SELECT sum(a.cents) FROM sales a JOIN sales b ON a.region=b.region WHERE a.region='west' AND a.day LIKE '2026-01-%'",
    "write_denied": "DELETE FROM sales WHERE id=1",
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sql", help="Evaluate one query against the January west-region total")
    args = parser.parse_args()
    verifier = Verifier(fixture_snapshot())
    candidates = {"candidate": args.sql} if args.sql is not None else CANDIDATES
    results = {name: verifier.score(sql, REFERENCE).to_dict() for name, sql in candidates.items()}
    print(json.dumps({"fixture": "synthetic in-memory sales", "model_trained": False,
                      "reference_total_cents": 5000, "results": results}, indent=2))
    return 1 if args.sql is not None and not results["candidate"]["correct"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
