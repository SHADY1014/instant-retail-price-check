"""Tests for local audit records of network city detection."""

import os
import sys
import tempfile
import unittest

TEST_DB = os.path.join(tempfile.gettempdir(), "test_network_city_audit.db")
if os.path.exists(TEST_DB):
    os.remove(TEST_DB)
os.environ["OCR_LEARNING_DB"] = TEST_DB
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database


class NetworkCityAuditTest(unittest.TestCase):
    def test_request_candidates_and_decision_are_persisted(self):
        authorized_at = database.record_network_city_consent()
        request_id = database.create_network_city_request(
            authorized_at, {"桂林市", "南宁市"}, ["甲店", "乙店"]
        )
        database.record_network_city_candidates(
            request_id, {"甲店": "桂林市", "乙店": ""}
        )
        database.record_network_city_decisions(
            request_id, {"甲店": "桂林市", "乙店": ""}
        )

        rows = database.list_network_city_requests()
        by_shop = {row["shop_name"]: row for row in rows}
        self.assertEqual(by_shop["甲店"]["candidate_city"], "桂林市")
        self.assertEqual(by_shop["甲店"]["final_city"], "桂林市")
        self.assertEqual(by_shop["乙店"]["candidate_city"], "")
        self.assertEqual(by_shop["乙店"]["final_city"], "")


if __name__ == "__main__":
    unittest.main()
