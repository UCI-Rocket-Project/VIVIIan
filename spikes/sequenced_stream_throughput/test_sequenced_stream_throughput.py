from __future__ import annotations

import unittest

from .example import run


class SequencedStreamThroughputTests(unittest.TestCase):
    def test_validates_100000_distinct_frames(self) -> None:
        result = run(count=100_000)
        self.assertEqual(result["count"], 100_000)
        self.assertGreater(result["frames_per_second"], 0.0)


if __name__ == "__main__":
    unittest.main()
