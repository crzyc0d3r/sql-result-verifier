"""Bounded, read-only SQL evaluation on an isolated synthetic SQLite snapshot."""
from collections import Counter
from contextlib import closing
from dataclasses import asdict, dataclass
import math
import sqlite3
import time


@dataclass(frozen=True)
class Result:
    executable: bool
    correct: bool
    reward: float
    vm_steps_upper_bound: int
    rows: tuple = ()
    reason: str = ""

    def to_dict(self):
        value = asdict(self)
        value["rows"] = [[{"blob_hex": x.hex()} if isinstance(x, bytes) else x for x in row]
                         for row in self.rows]
        return value


def fixture_snapshot():
    """Create only an in-memory, fictional sales fixture; never connect externally."""
    with closing(sqlite3.connect(":memory:")) as db:
        db.executescript("""
            CREATE TABLE sales(id INTEGER PRIMARY KEY, region TEXT, day TEXT, cents INTEGER);
            INSERT INTO sales VALUES
                (1,'west','2026-01-01',1000), (2,'west','2026-01-20',2000),
                (3,'east','2026-01-10',7000), (4,'west','2026-02-01',9000),
                (5,'west','2026-01-20',2000);
        """)
        return db.serialize()


class Verifier:
    """Gate efficiency rewards on exact rows; not a general hostile-code sandbox."""
    def __init__(self, snapshot, *, max_rows=1000, max_steps=100_000,
                 timeout_seconds=1.0, efficiency_weight=0.1, ordered=False):
        for name, value in (("max_rows", max_rows), ("max_steps", max_steps)):
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if (isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float))
                or not math.isfinite(timeout_seconds) or timeout_seconds <= 0):
            raise ValueError("timeout_seconds must be finite and positive")
        if (isinstance(efficiency_weight, bool) or not isinstance(efficiency_weight, (int, float))
                or not math.isfinite(efficiency_weight) or not 0 <= efficiency_weight < 1):
            raise ValueError("efficiency_weight must be finite and in [0, 1)")
        if not isinstance(snapshot, bytes) or not snapshot:
            raise ValueError("snapshot must be nonempty SQLite bytes")
        if type(ordered) is not bool:
            raise ValueError("ordered must be boolean")
        self.snapshot = snapshot
        self.max_rows, self.max_steps = max_rows, max_steps
        self.timeout_seconds, self.efficiency_weight = timeout_seconds, efficiency_weight
        self.ordered = ordered

    def _execute(self, sql):
        if not isinstance(sql, str) or not sql.strip() or len(sql) > 10_000:
            raise ValueError("SQL must contain 1 to 10000 characters")
        with closing(sqlite3.connect(":memory:")) as db:
            db.deserialize(self.snapshot)
            db.execute("PRAGMA query_only=ON")
            db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 100_000)
            db.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 10_000)
            db.setlimit(sqlite3.SQLITE_LIMIT_COLUMN, 100)
            allowed_functions = {"sum", "count", "min", "max", "avg", "abs", "round", "coalesce", "like"}
            allowed_actions = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_RECURSIVE}

            def authorize(action, first, second, database, trigger):
                if action == sqlite3.SQLITE_FUNCTION:
                    return sqlite3.SQLITE_OK if second in allowed_functions else sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK if action in allowed_actions else sqlite3.SQLITE_DENY

            db.set_authorizer(authorize)
            steps = 0
            started = time.monotonic()

            def progress():
                nonlocal steps
                steps += 1
                return steps >= self.max_steps or time.monotonic() - started > self.timeout_seconds

            db.set_progress_handler(progress, 1)
            cursor = db.execute(sql)
            rows = tuple(cursor.fetchmany(self.max_rows + 1))
            if len(rows) > self.max_rows:
                raise ValueError("result exceeds row budget")
            # SQLite values are scalar and hashable, including NULL and BLOBs.
            for row in rows:
                if any(isinstance(value, float) and not math.isfinite(value) for value in row):
                    raise ValueError("non-finite result values are not comparable")
            return rows, steps, len(cursor.description)

    def score(self, candidate, reference):
        """Reference errors fail the experiment, never become a candidate reward."""
        try:
            expected, _, expected_columns = self._execute(reference)
        except (sqlite3.Error, ValueError) as exc:
            raise ValueError(f"invalid reference query: {exc}") from exc
        try:
            rows, steps, columns = self._execute(candidate)
        except (sqlite3.Error, ValueError) as exc:
            return Result(False, False, 0.0, 0, reason=str(exc))
        same_rows = rows == expected if self.ordered else Counter(rows) == Counter(expected)
        correct = columns == expected_columns and same_rows
        if not correct:
            return Result(True, False, 0.0, steps, rows, "result mismatch")
        bonus = self.efficiency_weight / (1 + steps / 1000)
        return Result(True, True, 1.0 + bonus, steps, rows, "correct")
