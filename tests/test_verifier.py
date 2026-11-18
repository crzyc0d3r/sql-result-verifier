import unittest
from verifier import Verifier, fixture_snapshot
from app import CANDIDATES, REFERENCE


class VerifierTests(unittest.TestCase):
    def setUp(self):
        self.v = Verifier(fixture_snapshot())

    def test_equivalent_queries_pass(self):
        for key in ("correct", "equivalent"):
            with self.subTest(key=key):
                r = self.v.score(CANDIDATES[key], REFERENCE)
                self.assertTrue(r.correct)
                self.assertGreaterEqual(r.reward, 1)
                self.assertLess(r.reward, 1.1)

    def test_executable_wrong_queries_receive_no_efficiency_bonus(self):
        for key in ("wrong_region", "missing_filter", "duplicate_join"):
            with self.subTest(key=key):
                r = self.v.score(CANDIDATES[key], REFERENCE)
                self.assertTrue(r.executable)
                self.assertFalse(r.correct)
                self.assertEqual(r.reward, 0)

    def test_multiset_retains_duplicate_rows(self):
        self.assertFalse(self.v.score("SELECT cents FROM sales WHERE id IN (1,2)",
                                     "SELECT cents FROM sales WHERE id IN (1,2,5)").correct)

    def test_order_insensitive_by_default(self):
        self.assertTrue(self.v.score("SELECT id FROM sales ORDER BY id DESC",
                                    "SELECT id FROM sales ORDER BY id").correct)

    def test_order_sensitive_when_requested(self):
        ordered = Verifier(fixture_snapshot(), ordered=True)
        self.assertFalse(ordered.score("SELECT id FROM sales ORDER BY id DESC",
                                      "SELECT id FROM sales ORDER BY id").correct)

    def test_empty_result_projection_width_must_match(self):
        for ordered in (False, True):
            v = Verifier(fixture_snapshot(), ordered=ordered)
            self.assertFalse(v.score("SELECT id,region FROM sales WHERE id<0",
                                     "SELECT id FROM sales WHERE id<0").correct)

    def test_null_and_empty_results(self):
        self.assertTrue(self.v.score("SELECT NULL", "SELECT NULL").correct)
        self.assertTrue(self.v.score("SELECT id FROM sales WHERE id<0",
                                    "SELECT id FROM sales WHERE id>100").correct)

    def test_unsafe_operations_and_functions_rejected(self):
        for sql in (CANDIDATES["write_denied"], "UPDATE sales SET cents=0 WHERE id=1",
                    "DROP TABLE sales", "PRAGMA user_version=1", "ATTACH ':memory:' AS other",
                    "SELECT load_extension('nope')", "SELECT randomblob(1000000)"):
            with self.subTest(sql=sql):
                self.assertFalse(self.v.score(sql, "SELECT 1").executable)
        self.assertEqual(self.v.score(REFERENCE, REFERENCE).rows, ((5000,),))

    def test_multiple_statements_rejected(self):
        self.assertFalse(self.v.score("SELECT 1; SELECT 2", "SELECT 1").executable)

    def test_readonly_cte_accepted(self):
        self.assertTrue(self.v.score("WITH t AS (SELECT 1 AS n) SELECT n FROM t", "SELECT 1").correct)

    def test_steps_interrupt_infinite_query(self):
        v = Verifier(fixture_snapshot(), max_steps=500)
        r = v.score("WITH RECURSIVE t(n) AS (VALUES(1) UNION ALL SELECT n+1 FROM t) SELECT sum(n) FROM t", "SELECT 1")
        self.assertFalse(r.executable)
        self.assertIn("interrupt", r.reason)

    def test_row_budget(self):
        v = Verifier(fixture_snapshot(), max_rows=2)
        self.assertFalse(v.score("SELECT id FROM sales", "SELECT 1").executable)

    def test_invalid_reference_aborts(self):
        with self.assertRaisesRegex(ValueError, "invalid reference"):
            self.v.score("SELECT 1", "SELECT nope")

    def test_invalid_limits(self):
        for kwargs in ({"max_rows": True}, {"max_steps": 0}, {"timeout_seconds": float('nan')},
                       {"efficiency_weight": 1}, {"efficiency_weight": -0.1}, {"ordered": 1}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                Verifier(fixture_snapshot(), **kwargs)

    def test_nonfinite_results_rejected(self):
        self.assertFalse(self.v.score("SELECT 1e999", "SELECT 1").executable)

    def test_blob_results_are_json_serializable(self):
        import json
        result = self.v.score("SELECT X'01'", "SELECT X'01'")
        self.assertTrue(result.correct)
        self.assertIn("blob_hex", json.dumps(result.to_dict()))

    def test_query_validation(self):
        for query in (None, "", "x" * 10001, "SELECT missing FROM sales"):
            with self.subTest(query=str(query)[:30]):
                self.assertFalse(self.v.score(query, "SELECT 1").executable)


if __name__ == "__main__":
    unittest.main()
