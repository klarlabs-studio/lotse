import unittest

from agent_headroom_diagnostic import PRICE, attribute


def call(read=0, write=0, results=()):
    return {"usage": {"input_tokens": 0, "cache_read_input_tokens": read, "cache_creation_input_tokens": write},
            "results": list(results)}


class AttributeTest(unittest.TestCase):
    def test_a_result_pays_one_write_and_a_read_on_every_later_call(self):
        calls = [call(write=1000, results=[("search", 200)]), call(read=1000, write=200), call(read=1200), call(read=1200)]
        cost = attribute(calls, output=0)
        self.assertAlmostEqual(cost["tool:search"], 200 * (PRICE["cache_write_1h"] + 2 * PRICE["cache_read"]))
        self.assertAlmostEqual(cost["search_before_first_edit"], cost["tool:search"])
        self.assertAlmostEqual(cost["history"], 0)

    def test_new_tokens_beyond_the_results_are_carried_history_and_edits_end_the_search_phase(self):
        calls = [call(write=500, results=[("edit", 100)]), call(read=500, write=150, results=[("read", 50)]), call(read=650, write=50)]
        cost = attribute(calls, output=10)
        self.assertAlmostEqual(cost["history"], 50 * (PRICE["cache_write_1h"] + PRICE["cache_read"]))
        self.assertGreater(cost["tool:read"], 0)
        self.assertEqual(cost["search_before_first_edit"], 0)
        self.assertAlmostEqual(cost["output"], 10 * PRICE["output"])


if __name__ == "__main__":
    unittest.main()
