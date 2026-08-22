"""Regression tests for local legacy lookup and selected-city boundaries."""

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

# city_detector imports the learning database during module loading. Keep this
# lightweight test independent from a real user's writable data directory.
TEST_DIR = tempfile.mkdtemp(prefix="lq_city_scope_test_")
os.environ["OCR_LEARNING_DB"] = os.path.join(TEST_DIR, "ocr_learning.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import city_detector


class CityScopeTests(unittest.TestCase):
    def test_legacy_local_match_is_used_when_learning_db_misses(self):
        with patch.object(city_detector, "_batch_lookup_learned", return_value={}), \
             patch("shop_city_db.batch_lookup", return_value={"东莞店": "东莞市"}):
            matched = city_detector.batch_lookup_local_cities(["东莞店"], {"东莞市"})
        self.assertEqual(matched, {"东莞店": "东莞市"})

    def test_legacy_result_outside_selected_city_is_rejected(self):
        with patch.object(city_detector, "_batch_lookup_learned", return_value={}), \
             patch("shop_city_db.batch_lookup", return_value={"佛山店": "佛山市"}):
            matched = city_detector.batch_lookup_local_cities(["佛山店"], {"东莞市"})
        self.assertEqual(matched, {})

    def test_region_lookup_rejects_out_of_range_network_result(self):
        with patch.object(city_detector, "_search_baidu_map", return_value={"佛山市"}):
            city = city_detector.detect_city_in_region("测试店", {"东莞市"})
        self.assertEqual(city, "")

    def test_empty_selected_city_set_never_expands_to_all_cities(self):
        with patch.object(city_detector, "_search_baidu_map") as search:
            city = city_detector.detect_city_in_region("东莞店", set())
        self.assertEqual(city, "")
        search.assert_not_called()


if __name__ == "__main__":
    unittest.main()
