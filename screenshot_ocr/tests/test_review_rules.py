"""Tests for pre-export review rules."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from review_rules import find_review_issues


class ReviewRulesTest(unittest.TestCase):
    def setUp(self):
        self.valid_record = {
            "region": "桂林市",
            "shop_name": "测试便利店",
            "product_name": "漓泉1998 500ml*12瓶",
            "original_price": "65",
            "final_price": "60",
            "remark": "",
        }

    def test_complete_record_has_no_issues(self):
        self.assertEqual(find_review_issues(self.valid_record), [])

    def test_missing_critical_fields_are_reported(self):
        record = dict(self.valid_record, region="", shop_name="", product_name="")
        self.assertEqual(
            find_review_issues(record),
            ["未确认所属区域", "未识别店铺名称", "未识别产品名称"],
        )

    def test_missing_prices_are_reported(self):
        record = dict(self.valid_record, original_price="", final_price="0")
        self.assertEqual(
            find_review_issues(record),
            ["未识别产品原价", "未识别成交价"],
        )

    def test_ocr_failure_is_reported_once(self):
        record = dict(self.valid_record, remark="OCR失败: Vision service unavailable")
        self.assertEqual(find_review_issues(record), ["OCR 识别失败"])


if __name__ == "__main__":
    unittest.main()
