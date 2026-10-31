"""The t12 overhead diagnostic counts each model call once and prices a fixed prefix as one write plus reads."""
import json
from pathlib import Path
import tempfile
import unittest

from t12_overhead import calls, overhead_usd


class T12OverheadTest(unittest.TestCase):
    def test_a_message_streamed_in_several_events_is_one_call(self):
        usage = {"input_tokens": 2, "cache_creation_input_tokens": 10, "cache_read_input_tokens": 5}
        stream = Path(tempfile.mkdtemp()) / "agent-stream.jsonl"
        events = [{"type": "assistant", "message": {"id": "m1", "usage": usage}},
                  {"type": "assistant", "message": {"id": "m1", "usage": usage}},
                  {"type": "system", "message": "text"},
                  {"type": "assistant", "message": {"id": "m2", "usage": usage}}]
        stream.write_text("".join(json.dumps(e) + "\n" for e in events))
        self.assertEqual(len(calls(stream)), 2)

    def test_overhead_is_one_cache_write_then_a_read_per_later_call(self):
        self.assertAlmostEqual(overhead_usd(1_000_000, 3), 0.2 + 2 * 0.01)


if __name__ == "__main__":
    unittest.main()
