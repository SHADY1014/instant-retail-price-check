"""Regression tests for recoverable Windows OCR worker failures."""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main


class OcrWorkerFailureTests(unittest.TestCase):

    def test_engine_initialization_failure_emits_retryable_results(self):
        emitted = []
        worker = main.OCRWorker(["one.png", "two.png"])
        worker.finished_ocr.connect(lambda *args: emitted.append(args))

        with patch.object(main, "run_ocr_parallel",
                          side_effect=RuntimeError("RapidOCR unavailable")):
            worker.run()

        self.assertEqual(len(emitted), 1)
        results, retry_paths, cancelled = emitted[0]
        self.assertFalse(cancelled)
        self.assertEqual(set(retry_paths), {"one.png", "two.png"})
        self.assertIn("RapidOCR unavailable", results["one.png"].remark)


if __name__ == "__main__":
    unittest.main()
