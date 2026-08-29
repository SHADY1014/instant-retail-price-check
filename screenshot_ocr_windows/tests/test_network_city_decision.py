"""Regression tests for evidence-based network city decisions."""

import os
import sys
import tempfile
import unittest
from unittest.mock import patch


TEST_DIR = tempfile.mkdtemp(prefix="lq_network_city_test_")
os.environ["OCR_LEARNING_DB"] = os.path.join(TEST_DIR, "ocr_learning.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import city_detector


class NetworkCityDecisionTests(unittest.TestCase):

    def test_search_queries_keep_branch_name_before_shorter_fallbacks(self):
        queries = city_detector._build_search_queries("24客超市（友谊路店）")

        self.assertEqual(queries[0], "24客超市（友谊路店）")
        self.assertIn("24客超市", queries)

    def test_poi_name_evidence_beats_alphabetical_city_order(self):
        shop_name = "24客超市（友谊路店）"
        candidates = [
            city_detector.PoiCandidate(
                city="南宁市", poi_name="24客超市（民族大道店）", rank=0,
                query=shop_name,
            ),
            city_detector.PoiCandidate(
                city="贵阳市", poi_name=shop_name, rank=1, query=shop_name,
            ),
        ]

        with patch.object(city_detector, "_lookup_learned_city", return_value=""), \
             patch.object(
                 city_detector, "_search_baidu_candidates", return_value=candidates):
            decision = city_detector.detect_city_decision_in_region(
                shop_name, {"南宁市", "贵阳市"})

        self.assertEqual(decision.city, "贵阳市")
        self.assertTrue(decision.auto_accept)
        self.assertGreater(
            decision.candidates[0].score, decision.candidates[1].score)

    def test_ambiguous_poi_candidates_require_manual_review(self):
        shop_name = "同名便利店"
        candidates = [
            city_detector.PoiCandidate(
                city="广州市", poi_name=shop_name, rank=0, query=shop_name,
            ),
            city_detector.PoiCandidate(
                city="深圳市", poi_name=shop_name, rank=0, query=shop_name,
            ),
        ]

        with patch.object(city_detector, "_lookup_learned_city", return_value=""), \
             patch.object(
                 city_detector, "_search_baidu_candidates", return_value=candidates):
            decision = city_detector.detect_city_decision_in_region(
                shop_name, {"广州市", "深圳市"})
            city = city_detector.detect_city_in_region(
                shop_name, {"广州市", "深圳市"})

        self.assertFalse(decision.auto_accept)
        self.assertEqual(city, "")
        self.assertEqual({item.city for item in decision.candidates}, {"广州市", "深圳市"})

    def test_single_weak_network_candidate_is_not_auto_filled(self):
        candidates = [
            city_detector.PoiCandidate(
                city="广州市", poi_name="无关地点", rank=9, query="测试店",
            ),
        ]

        with patch.object(city_detector, "_lookup_learned_city", return_value=""), \
             patch.object(
                 city_detector, "_search_baidu_candidates", return_value=candidates):
            decision = city_detector.detect_city_decision_in_region(
                "测试店", {"广州市"})

        self.assertEqual(decision.city, "广州市")
        self.assertFalse(decision.auto_accept)


if __name__ == "__main__":
    unittest.main()
