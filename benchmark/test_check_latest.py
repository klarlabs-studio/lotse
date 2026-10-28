"""Every pinned tool must be at its latest release before a run is frozen."""
import unittest

from check_latest import behind, version_key


class CheckLatest(unittest.TestCase):
    def test_versions_compare_numerically(self):
        self.assertLess(version_key("0.9.95"), version_key("0.9.100"))
        self.assertEqual(version_key("2.1.307 (Claude Code)"), (2, 1, 307))

    def test_only_tools_behind_their_latest_release_are_reported(self):
        pinned = {"graphify": "0.9.95", "claude-code": "2.1.303 (Claude Code)"}
        latest = {"graphify": "0.9.95", "claude-code": "2.1.307"}
        self.assertEqual(behind(pinned, latest), {"claude-code": ("2.1.303 (Claude Code)", "2.1.307")})


if __name__ == "__main__":
    unittest.main()
