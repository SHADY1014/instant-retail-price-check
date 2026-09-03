"""Regression tests for crash-safe network city callbacks."""

import os
import sys
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main


class RegionNetworkWorkerTests(unittest.TestCase):
    def test_worker_emits_state_with_results(self):
        emitted = []
        worker = main.RegionNetworkWorker(["甲店"], {"南宁市"})
        worker.finished_details.connect(emitted.append)
        decision = types.SimpleNamespace(
            auto_accept=True, city="南宁市", candidates=(),
        )

        with patch.object(main, "batch_lookup_local_cities", return_value={}), \
                patch.object(main, "detect_city_decision_in_region",
                             return_value=decision), \
                patch.object(main.time, "sleep"):
            worker.run()

        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0]["results"], {"甲店": "南宁市"})
        self.assertEqual(emitted[0]["network_shops"], ("甲店",))
        self.assertEqual(emitted[0]["restrict_cities"], ("南宁市",))
        self.assertFalse(emitted[0]["error"])

    def test_worker_failure_is_returned_without_raising(self):
        emitted = []
        worker = main.RegionNetworkWorker(["甲店"], {"南宁市"})
        worker.finished_details.connect(emitted.append)

        with patch.object(main, "batch_lookup_local_cities",
                          side_effect=RuntimeError("network setup failed")):
            worker.run()

        self.assertEqual(len(emitted), 1)
        self.assertIn("network setup failed", emitted[0]["error"])

    def test_callback_exception_is_isolated_from_qt(self):
        class Button:
            def __init__(self):
                self.enabled = False

            def setEnabled(self, value):
                self.enabled = value

        class Label:
            def __init__(self):
                self.text = ""

            def setText(self, value):
                self.text = value

        window = main.MainWindow.__new__(main.MainWindow)
        window._region_worker = object()
        window._network_running = True
        window.net_city_btn = Button()
        window.status_label = Label()
        window._on_region_cities_detected_impl = (
            lambda worker, payload: (_ for _ in ()).throw(
                RuntimeError("callback bug")))

        with patch.object(main.QMessageBox, "critical"):
            window._on_region_cities_detected(window._region_worker, {})

        self.assertFalse(window._network_running)
        self.assertTrue(window.net_city_btn.enabled)
        self.assertIn("失败", window.status_label.text)

    def test_repeated_request_is_rejected_while_worker_runs(self):
        window = main.MainWindow.__new__(main.MainWindow)
        window._network_running = False
        window._region_worker = types.SimpleNamespace(isRunning=lambda: True)

        with patch.object(main.QMessageBox, "information") as info:
            window._network_detect_city()

        info.assert_called_once()


if __name__ == "__main__":
    unittest.main()
