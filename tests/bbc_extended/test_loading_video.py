"""Cold loading video executes every edge outside the short Python checks."""

import unittest

from tools.bbc.loading_video import qualify


class LoadingVideoTests(unittest.TestCase):
    def test_first_second_of_cold_loading(self):
        self.assertTrue(qualify()["passed"])


if __name__ == "__main__":
    unittest.main()
