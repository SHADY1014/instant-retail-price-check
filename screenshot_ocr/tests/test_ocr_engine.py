"""Tests for cancellable OCR scheduling."""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ocr_engine


class OCRSchedulingTest(unittest.TestCase):
    def test_cancellation_stops_new_submissions(self):
        calls = []
        cancel = {"requested": False}

        def fake_ocr(path):
            calls.append(path)
            cancel["requested"] = True
            return [{"text": path}]

        with patch.object(ocr_engine, "run_ocr", side_effect=fake_ocr):
            results = ocr_engine.run_ocr_parallel(
                ["first", "second", "third"],
                max_workers=1,
                should_cancel=lambda: cancel["requested"],
            )

        self.assertEqual(calls, ["first"])
        self.assertEqual(list(results), ["first"])


if __name__ == "__main__":
    unittest.main()
