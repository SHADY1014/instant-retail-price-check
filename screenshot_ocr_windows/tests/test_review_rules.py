from review_rules import find_review_issues
import unittest


class ReviewRuleTests(unittest.TestCase):
    def test_complete_record_passes(self):
        self.assertEqual(find_review_issues({
            "region": "广州市",
            "shop_name": "测试店",
            "product_name": "燕京U8",
            "original_price": "60",
            "final_price": "55",
            "remark": "",
        }), [])


    def test_missing_fields_are_reported(self):
        issues = find_review_issues({
            "region": "",
            "shop_name": "",
            "product_name": "燕京U8",
            "original_price": "0",
            "final_price": "",
            "remark": "",
        })
        self.assertEqual(issues, ["未确认所属区域", "未识别店铺名称", "未识别产品原价", "未识别成交价"])


    def test_failed_ocr_is_retryable_review(self):
        self.assertEqual(find_review_issues({"remark": "OCR失败: model"}), ["OCR 识别失败"])

    def test_cancelled_ocr_is_retryable_review(self):
        self.assertEqual(
            find_review_issues({"remark": "OCR 未完成：已取消，可重试"}),
            ["OCR 识别失败"],
        )
